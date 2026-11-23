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
    if (selector === '.lead-form') return this.children.find((child) => child.className?.includes('lead-form')) ?? null;
    if (!this.selectors.has(selector)) this.selectors.set(selector, new Element());
    return this.selectors.get(selector);
  }

  focus() {
    document.activeElement = this;
  }

  setAttribute(name, value) {
    this.attributes.set(name, value);
  }

  get childElementCount() {
    return this.children.length;
  }

  remove() {
    for (const parent of [get('chatMessages')]) parent.children = parent.children.filter((child) => child !== this);
  }
}

const nodes = new Map();
const opener = new Element();
const document = new Element();
document.body = new Element();
document.createElement = () => new Element();
document.createTextNode = (text) => text;
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

const requests = [];
let failNext = false;
async function fetch(url, options) {
  const body = JSON.parse(options.body);
  requests.push({url, body});
  if (failNext) {
    failNext = false;
    return {ok: false, json: async () => ({detail: 'Muitas mensagens em pouco tempo.'})};
  }
  const data = url.endsWith('/chat') ? {reply: `Resposta ${body.messages.length}`} : {status: 'enviado'};
  return {ok: true, json: async () => data};
}
class FormData {
  constructor(form) { this.form = form; }
  get(name) { return this.form.values[name] ?? ''; }
}
const context = createContext({window: {}, document, fetch, FormData, TypeError, Error});
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
assert.equal(get('caseCount').textContent, '1 aplicação encontrada');
get('caseArea').value = 'Integração';
get('caseArea').dispatch('input');
assert.equal(get('caseCount').textContent, '0 aplicações encontradas');
get('caseArea').value = '';
get('caseArea').dispatch('input');

const caseButton = get('casesGrid').children[0].querySelector('.case-button');
caseButton.focus();
caseButton.dispatch('click');
assert.equal(get('chatOverlay').hidden, false);

(async () => {
const tick = () => new Promise((resolve) => setImmediate(resolve));
const texts = () => get('chatMessages').children.map((child) => child.children[1]?.textContent);
assert.match(get('chatInput').value, /Conciliação de dados e relatórios/);
assert.match(texts()[0], /assistente virtual da FuturaNext/);
assert.deepEqual(get('chatChoices').children.map((item) => item.textContent).at(-1), 'Falar com a equipe');

get('chatForm').dispatch('submit');
await tick();
assert.equal(requests[0].url, '/api/v1/public/chat');
assert.deepEqual(requests[0].body.messages.map((item) => item.role), ['user']);
assert.equal(texts().at(-1), 'Resposta 1');
assert.equal(get('chatInput').value, '');

get('chatInput').value = 'Quanto custa?';
get('chatForm').dispatch('submit');
await tick();
assert.deepEqual(requests[1].body.messages.map((item) => item.role), ['user', 'assistant', 'user']);

failNext = true;
get('chatInput').value = 'Outra pergunta';
get('chatForm').dispatch('submit');
await tick();
assert.match(texts().at(-1), /Muitas mensagens em pouco tempo\. .*suporte@futuranext\.com\.br/);

get('chatChoices').children.at(-1).dispatch('click');
const lead = get('chatMessages').children.at(-1);
assert.equal(lead.className, 'chat-finished lead-form');
lead.values = {name: 'Pessoa', company: 'Empresa', contact: '19 98888-7777', message: ''};
const consent = lead.children.find((child) => child.className === 'lead-consent').children[0];
consent.checked = true;
lead.dispatch('submit');
await tick();
const sent = requests.at(-1);
assert.equal(sent.url, '/api/v1/public/leads');
assert.equal(sent.body.consent, true);
assert.equal(sent.body.transcript.length, 4);
assert.match(texts().at(-1), /Recebemos seu contato/);
assert.ok(!get('chatChoices').children.some((item) => item.textContent === 'Falar com a equipe'));

document.dispatch('keydown', {key: 'Escape'});
assert.equal(get('chatOverlay').hidden, true);
assert.equal(document.activeElement, caseButton);

console.log('DOM simulado: menu, calculadora, filtros, chat com IA, contato e foco passaram.');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
