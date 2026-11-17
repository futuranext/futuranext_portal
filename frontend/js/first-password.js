import {api, ApiError, auth} from './client.js';
import {requireMfa} from './mfa.js';
import {actionForm, focusHeading, inputField, link, muted, notice, panel, UserMessage} from './ui.js';

const endpoint = '/auth/first-password';

function showPasswordForm(target, login) {
  const newPassword = {type: 'password', autocomplete: 'new-password', minlength: 12, maxlength: 72, required: true};
  const form = actionForm([
    inputField('Senha temporária', 'temporary_password', {type: 'password', autocomplete: 'current-password', required: true}),
    inputField('Nova senha', 'new_password', newPassword, 'Use pelo menos 12 caracteres.'),
    inputField('Confirme a nova senha', 'confirmation', newPassword),
  ], 'Salvar nova senha', async (data, form) => {
    const temporary = data.get('temporary_password');
    const password = data.get('new_password');
    if (password !== data.get('confirmation')) throw new UserMessage('As duas senhas precisam ser iguais.');
    if (password === temporary) throw new UserMessage('Escolha uma senha diferente da temporária.');
    if (new TextEncoder().encode(password).length > 72) {
      throw new UserMessage('A senha ultrapassou o limite. Use uma senha um pouco mais curta.');
    }

    form.reset();
    for (const name of ['temporary_password', 'new_password', 'confirmation']) data.delete(name);
    try {
      await api(endpoint, {method: 'PUT', body: {temporary_password: temporary, new_password: password}});
    } catch (error) {
      if (error instanceof ApiError && error.status === 400) throw new UserMessage('A senha temporária não confere.');
      throw error;
    }
    target.replaceChildren(panel('Senha atualizada',
      notice('Entre novamente com a nova senha para continuar.'), link(login, 'Ir para o login', 'button'),
    ));
    focusHeading(target);
    try {
      await auth.signOut({scope: 'local'});
    } finally {
      location.replace(login);
    }
  }, {singleUse: true});
  form.setAttribute('method', 'post');

  target.replaceChildren(panel('Troque sua senha temporária',
    muted('Confirme a senha temporária e escolha a senha que você usará para entrar.'), form,
  ));
  focusHeading(target);
}

export async function requireFirstPassword(target, login) {
  let status;
  try {
    status = await api(endpoint);
  } catch (error) {
    // O frontend pode ser publicado antes da API que adiciona este endpoint.
    if (error instanceof ApiError && error.status === 404) return false;
    throw error;
  }
  if (typeof status?.required !== 'boolean') {
    throw new UserMessage('Não foi possível verificar a configuração da senha. Tente novamente.');
  }
  if (!status.required) return false;

  const {data, error} = await auth.mfa.getAuthenticatorAssuranceLevel();
  if (error) throw new UserMessage('Não foi possível verificar a segurança da sessão. Tente entrar novamente.');
  if (data.nextLevel === 'aal2' && data.currentLevel !== 'aal2') {
    await requireMfa(target, {allowEnrollment: false, onVerified: () => showPasswordForm(target, login)});
  } else {
    showPasswordForm(target, login);
  }
  return true;
}
