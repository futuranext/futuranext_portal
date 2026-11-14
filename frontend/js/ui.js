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
