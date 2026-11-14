// Execute da raiz com: node docs/fixtures/landing-dom-smoke.cjs
// Exercita a lógica com um DOM e clipboard simulados. Layout e APIs reais
// do navegador exigem o roteiro manual de docs/TESTES.md.

const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {createContext, runInContext} = require('node:vm');

class Element {
  children = [];
  listeners = new Map();
  selectors = new Map();
  attributes = new Map();
  classes = new Set();
  value = '';
  textContent = '';
  hidden = false;
  style = {};
  classList = {
    add: (name) => this.classes.add(name),
    remove: (name) => this.classes.delete(name),
    toggle: (name) => {
      if (this.classes.delete(name)) return false;
      this.classes.add(name);
      return true;
    },
  };

  addEventListener(event, listener) {
    if (!this.listeners.has(event)) this.listeners.set(event, []);
    this.listeners.get(event).push(listener);
  }

  dispatch(event, details = {}) {
    for (const listener of this.listeners.get(event) ?? []) {
      listener({target: this, preventDefault() {}, ...details});
    }
  }

  append(...children) {
    this.children.push(...children);
  }

  appendChild(child) {
    this.append(child);
    return child;
  }

  replaceChildren(...children) {
    this.children = [...children];
  }

  querySelectorAll(selector) {
    return selector === 'a' ? this.children : [];
  }

  querySelector(selector) {
    if (!this.selectors.has(selector)) this.selectors.set(selector, new Element());
    return this.selectors.get(selector);
  }

  focus() {
    document.activeElement = this;
  }

  setAttribute(name, value) {
    this.attributes.set(name, value);
  }
}

const nodes = new Map();
const opener = new Element();
const document = new Element();
document.body = new Element();
document.createElement = () => new Element();
document.getElementById = (id) => {
  if (!nodes.has(id)) nodes.set(id, new Element());
  return nodes.get(id);
};
document.querySelectorAll = (selector) => selector === '.js-open-chat' ? [opener] : [];
const get = document.getElementById;

get('people').value = 4;
get('hours').value = 2;
get('salary').value = 3500;
get('caseTemplate').content = {cloneNode: () => new Element()};
get('chatOverlay').hidden = true;
const navAnchor = get('mainNav').appendChild(new Element());

let clipboard = '';
const navigator = {clipboard: {writeText: async (value) => { clipboard = value; }}};
const context = createContext({window: {}, document, navigator});
for (const script of ['config.js', 'script.js']) {
  runInContext(readFileSync(script, 'utf8'), context, {filename: script});
}

assert.equal(get('monthlyResult').textContent.replace(/\s/g, ''), 'R$2.800');
get('hours').value = 2.5;
get('hours').dispatch('input');
assert.equal(get('monthlyResult').textContent.replace(/\s/g, ''), 'R$3.500');
assert.equal(get('hoursValue').textContent, '2,5 h');

get('menuToggle').dispatch('click');
assert.equal(get('menuToggle').attributes.get('aria-expanded'), 'true');
navAnchor.dispatch('click');
assert.equal(get('menuToggle').attributes.get('aria-expanded'), 'false');

assert.equal(get('casesGrid').children.length, 9);
get('caseSearch').value = 'CONCILIACAO';
get('caseSearch').dispatch('input');
assert.equal(get('caseCount').textContent, '1 exemplo encontrado');
get('caseArea').value = 'Integração';
get('caseArea').dispatch('input');
assert.equal(get('caseCount').textContent, '0 exemplos encontrados');
get('caseArea').value = '';
get('caseArea').dispatch('input');

const caseButton = get('casesGrid').children[0].querySelector('.case-button');
caseButton.focus();
caseButton.dispatch('click');
assert.equal(get('chatOverlay').hidden, false);

const answers = [
  'Pessoa Teste',
  'Empresa Exemplo',
  'Automação RPA',
  'Desafio fictício para o teste',
  'Nos próximos 30 dias',
  'teste@example.invalid',
];
function submit(value) {
  get('chatInput').value = value;
  get('chatForm').dispatch('submit');
}

submit('a');
assert.match(get('chatHelper').textContent, /pelo menos 2/);
answers.forEach(submit);
get('chatChoices').children[0].dispatch('click');
assert.equal(get('chatForm').hidden, true);
assert.equal(get('chatProgress').style.width, '100%');

const actions = get('chatMessages').children.at(-1).children;
assert.deepEqual(actions.map((action) => action.textContent), [
  'Baixar resumo (.txt)',
  'Copiar resumo',
  'Nova conversa',
]);
actions[1].dispatch('click');
assert.match(clipboard, /Referência de interesse: Conciliação de dados e relatórios/);
assert.match(clipboard, /Autorização para contato: Sim, autorizo/);

document.dispatch('keydown', {key: 'Escape'});
assert.equal(get('chatOverlay').hidden, true);
assert.equal(document.activeElement, caseButton);

opener.dispatch('click');
answers.forEach(submit);
get('chatChoices').children[1].dispatch('click');
assert.equal(get('chatMessages').children.at(-1).className, 'chat-message bot');
assert.equal(get('chatForm').hidden, true);

console.log('DOM simulado: menu, calculadora, filtros, chat, autorização, resumo e foco passaram.');
