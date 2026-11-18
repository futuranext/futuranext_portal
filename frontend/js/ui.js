export const screen = document.getElementById('screen');

export function element(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value === true) node.setAttribute(name, '');
    else if (value !== false && value != null) node.setAttribute(name, String(value));
  }
  node.append(...children.flat().filter((child) => child != null));
  return node;
}

export const button = (text, secondary = false) => element('button', {type: 'button', class: secondary ? 'button secondary' : 'button'}, text);
export const link = (href, text, className) => element('a', {href, class: className}, text);
export const muted = (text) => element('p', {class: 'muted'}, text);
export const notice = (text, failed = false) => element('p', {class: failed ? 'notice error' : 'notice', role: failed ? 'alert' : 'status'}, text);
export const statusMessage = () => element('p', {class: 'status-message', role: 'status', 'aria-atomic': 'true'});
export const panel = (heading, ...content) => element('section', {class: 'panel'}, element('h2', {}, heading), ...content);

export function field(label, control, hint) {
  return element('label', {class: 'field'}, element('span', {}, label), control, hint && element('small', {}, hint));
}

export function inputField(label, name, attributes = {}, hint) {
  return field(label, element('input', {name, ...attributes}), hint);
}

export function checkField(label, name, checked) {
  return element('label', {class: 'check'}, element('input', {type: 'checkbox', name, checked}), label);
}

export function select(options, attributes = {}, selected = '') {
  const control = element('select', attributes, options.map(([value, name]) => element('option', {value}, name)));
  control.value = selected;
  return control;
}

export function runAction(control, status, action, {singleUse = false} = {}) {
  return async () => {
    if (control.disabled) return;
    control.disabled = true;
    control.setAttribute('aria-busy', 'true');
    status.className = 'status-message';
    status.textContent = '';
    let completed = false;
    try {
      const message = await action();
      completed = true;
      if (message) {
        status.classList.add('success');
        status.textContent = message;
      }
    } catch (error) {
      status.classList.add('error');
      status.textContent = error instanceof UserMessage ? error.message : 'Ocorreu um erro. Tente novamente.';
    } finally {
      control.disabled = completed && singleUse;
      control.removeAttribute('aria-busy');
    }
  };
}

export function actionForm(content, submitText, submit, {singleUse = false} = {}) {
  const control = button(submitText);
  control.type = 'submit';
  const status = statusMessage();
  const form = element('form', {class: 'stack compact'}, content, element('div', {class: 'form-actions'}, control), status);
  const perform = runAction(control, status, () => submit(new FormData(form), form), {singleUse});
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    void perform();
  });
  return form;
}

export function definitionList(entries) {
  return element('dl', {class: 'details'}, entries.map(([label, text]) => (
    element('div', {}, element('dt', {}, label), element('dd', {}, text))
  )));
}

export function focusHeading(container) {
  const heading = container.querySelector('h2, p[role="status"], p[role="alert"]');
  if (heading) {
    heading.setAttribute('tabindex', '-1');
    heading.focus();
  }
}

export function dateTime(value) {
  return new Intl.DateTimeFormat('pt-BR', {dateStyle: 'short', timeStyle: 'short'}).format(new Date(value));
}

export const roleNames = Object.freeze({
  super_admin: 'Administrador FuturaNext',
  suporte: 'Suporte FuturaNext',
  cliente_admin: 'Administrador da empresa',
  cliente_usuario: 'Usuário da empresa',
});

export class UserMessage extends Error {}

const normalize = (text) => String(text).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim();
let comboboxCount = 0;

/**
 * Transforma um <select> em campo de busca com sugestões enquanto digita.
 * O <select> continua sendo a fonte do valor (`.value`, `.options`, evento `change`).
 * Com `source(term)`, as opções vêm do servidor: deve devolver [[valor, rótulo], ...].
 */
export function combobox(control, {placeholder = 'Digite para buscar', source, minLength = source ? 2 : 0, limit = 50} = {}) {
  const id = `combobox-${++comboboxCount}`;
  const input = element('input', {
    type: 'text', role: 'combobox', autocomplete: 'off', spellcheck: 'false', placeholder,
    'aria-autocomplete': 'list', 'aria-expanded': 'false', 'aria-controls': `${id}-list`,
  });
  const list = element('ul', {id: `${id}-list`, role: 'listbox', class: 'combobox-list', hidden: true});
  control.hidden = true;
  control.tabIndex = -1;
  let items = [];
  let shown = [];
  let active = -1;
  let timer;
  let query = 0;

  const choices = () => Array.from(control.options).filter((option) => option.value && !option.disabled);
  const label = () => control.selectedOptions[0]?.value ? control.selectedOptions[0].textContent : '';
  const descriptor = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
  Object.defineProperty(control, 'value', {
    configurable: true,
    get: () => descriptor.get.call(control),
    set: (value) => {
      descriptor.set.call(control, value);
      input.value = label();
    },
  });

  function close() {
    list.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    active = -1;
  }

  function highlight(index) {
    active = index;
    items.forEach((item, position) => item.setAttribute('aria-selected', String(position === index)));
    if (index >= 0) {
      input.setAttribute('aria-activedescendant', items[index].id);
      items[index].scrollIntoView({block: 'nearest'});
    } else input.removeAttribute('aria-activedescendant');
  }

  function choose(option) {
    close();
    const changed = control.value !== option.value;
    control.value = option.value;
    if (changed) control.dispatchEvent(new Event('change', {bubbles: true}));
  }

  function render(message) {
    const term = normalize(input.value);
    const matches = source ? choices() : choices().filter((option) => !term || normalize(option.textContent).includes(term));
    shown = matches.slice(0, limit);
    items = shown.map((option, index) => {
      const item = element('li', {id: `${id}-${index}`, role: 'option', 'aria-selected': 'false'}, option.textContent);
      item.addEventListener('mousedown', (event) => event.preventDefault());
      item.addEventListener('click', () => choose(option));
      return item;
    });
    const empty = message ?? (matches.length ? null : 'Nenhum resultado encontrado.');
    const notes = [empty, matches.length > limit ? 'Continue digitando para refinar.' : null].filter(Boolean)
      .map((text) => element('li', {class: 'combobox-empty', role: 'presentation'}, text));
    list.replaceChildren(...items, ...notes);
    list.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    highlight(items.length ? 0 : -1);
  }

  async function search() {
    const term = input.value.trim();
    if (term.length < minLength) {
      render(`Digite pelo menos ${minLength} caracteres.`);
      return;
    }
    const current = ++query;
    render('Buscando…');
    try {
      const options = await source(term);
      if (current !== query) return;
      const keep = Array.from(control.options).filter((option) => !option.value || option.selected);
      const known = new Set(keep.map((option) => option.value));
      control.replaceChildren(...keep, ...options.filter(([value]) => !known.has(value))
        .map(([value, text]) => element('option', {value}, text)));
      render();
    } catch (error) {
      if (current === query) render(error instanceof Error && error.message ? error.message : 'Não foi possível buscar agora.');
    }
  }

  input.addEventListener('input', () => {
    if (!source) {
      render();
      return;
    }
    clearTimeout(timer);
    timer = setTimeout(() => void search(), 250);
  });
  input.addEventListener('focus', () => {
    input.select();
    if (!source) render();
  });
  input.addEventListener('blur', () => {
    clearTimeout(timer);
    close();
    input.value = label();
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (list.hidden) {
        if (source) void search(); else render();
        return;
      }
      if (!items.length) return;
      const step = event.key === 'ArrowDown' ? 1 : -1;
      highlight((active + step + items.length) % items.length);
    } else if (event.key === 'Enter') {
      if (list.hidden) return;
      event.preventDefault();
      if (shown[active]) choose(shown[active]);
    } else if (event.key === 'Escape' && !list.hidden) {
      event.preventDefault();
      close();
      input.value = label();
    }
  });

  input.value = label();
  return element('div', {class: 'combobox'}, input, list, control);
}

/** Campo que filtra, enquanto digita, os itens de uma lista já exibida. */
export function filterField(label, list, {placeholder = 'Digite para filtrar', text = (node) => node.textContent} = {}) {
  const input = element('input', {type: 'search', autocomplete: 'off', spellcheck: 'false', placeholder});
  const counter = element('small', {'aria-live': 'polite'});
  input.addEventListener('input', () => {
    const term = normalize(input.value);
    let visible = 0;
    for (const node of list.children) {
      if (node.dataset.filterIgnore != null) continue;
      const show = !term || normalize(text(node)).includes(term);
      node.hidden = !show;
      if (show) visible += 1;
    }
    counter.textContent = term ? (visible ? `${visible} resultado(s).` : 'Nenhum resultado encontrado.') : '';
  });
  return element('label', {class: 'field list-filter'}, element('span', {}, label), input, counter);
}
