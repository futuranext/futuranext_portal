import {api, resource} from './client.js';
import {actionForm, element, inputField, muted, UserMessage} from './ui.js';

export const userRemovedMessage = 'Usuário excluído. O e-mail está disponível para um novo cadastro.';

export function userRemoval(account, {currentUserId, signal, onRemoved}) {
  if (!account.email || account.user_id === currentUserId) return null;
  const form = actionForm([
    element('p', {}, 'A exclusão é permanente. Ela encerra o acesso em todas as empresas e apaga o histórico pessoal no portal. Registros das empresas, como faturas, são preservados.'),
    muted('O mesmo e-mail poderá ser cadastrado novamente como uma nova conta.'),
    inputField('Digite o e-mail do usuário para confirmar', 'email', {
      type: 'email', autocomplete: 'off', autocapitalize: 'none', spellcheck: 'false', maxlength: 254, required: true,
    }),
  ], 'Excluir usuário permanentemente', async (data) => {
    const email = data.get('email').trim().toLowerCase();
    if (email !== account.email.toLowerCase()) throw new UserMessage('Digite o e-mail do usuário selecionado.');
    await api(`/admin/users/${resource(account.user_id)}`, {method: 'DELETE', body: {email}, signal});
    if (!signal?.aborted) onRemoved(email);
  }, {singleUse: true});
  return element('details', {class: 'user-removal'}, element('summary', {}, 'Excluir usuário'), form);
}
