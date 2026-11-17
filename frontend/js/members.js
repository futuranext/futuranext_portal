import {api, resource} from './client.js';
import {actionForm, checkField, dateTime, element, field, inputField, link, muted, panel, roleNames, select, UserMessage} from './ui.js';

const memberRoles = [
  ['cliente_usuario', roleNames.cliente_usuario],
  ['cliente_admin', roleNames.cliente_admin],
];
const deliveryLabels = {
  sent: 'Enviado por e-mail',
  existing_account: 'Disponível na conta da pessoa convidada',
  pending: 'Envio pendente',
};

const temporaryPasswordInput = {
  type: 'password', autocomplete: 'new-password', minlength: 12, maxlength: 72, required: true,
};

export async function members(target, path, {canEdit, canAssignAdmin, canCreateWithTemporaryPassword = false, signal}) {
  const result = await api(path, {signal});
  if (signal?.aborted) return;
  const memberList = element('div', {class: 'stack compact'});
  const inviteList = element('ul', {class: 'record-list'});
  const roles = canAssignAdmin ? memberRoles : memberRoles.slice(0, 1);

  function renderUsers() {
    memberList.replaceChildren(...result.members.map((member) => {
      const display = `${member.email || 'E-mail indisponível'} · ${roleNames[member.role] ?? member.role} · ${member.active ? 'Ativo' : 'Inativo'}`;
      if (!canEdit || !roles.some(([role]) => role === member.role)) return muted(display);
      const summary = element('summary', {}, display);
      const edit = actionForm([
        field('Perfil', select(roles, {name: 'role', required: true}, member.role)),
        checkField('Vínculo ativo', 'active', member.active),
      ], 'Salvar usuário', async (data) => {
        const role = data.get('role');
        const active = data.has('active');
        await api(`${path}/${resource(member.user_id)}`, {method: 'PATCH', body: {role, active}, signal});
        member.role = role;
        member.active = active;
        summary.textContent = `${member.email || 'E-mail indisponível'} · ${roleNames[role]} · ${active ? 'Ativo' : 'Inativo'}`;
        return 'Vínculo atualizado.';
      });
      return element('details', {class: 'edit-disclosure'}, summary, edit);
    }));
    if (!memberList.childElementCount) memberList.append(muted('Nenhum usuário cadastrado.'));
  }

  function renderInvitations() {
    inviteList.replaceChildren(...result.invitations.map((invitation) => {
      const status = invitation.accepted_at ? 'Aceito' : deliveryLabels[invitation.delivery] ?? 'Pendente';
      return element('li', {class: 'record'},
        element('div', {class: 'stack compact'}, element('strong', {}, invitation.email),
          muted(`${roleNames[invitation.role] ?? invitation.role} · ${status} · Validade ${dateTime(invitation.expires_at)}`)),
      );
    }));
    if (!inviteList.childElementCount) inviteList.append(element('li', {}, muted('Nenhum convite registrado.')));
  }

  renderUsers();
  renderInvitations();
  const inviteForm = canEdit ? actionForm([
    element('div', {class: 'form-grid'},
      inputField('E-mail', 'email', {type: 'email', maxlength: 254, required: true}),
      field('Perfil', select(roles, {name: 'role', required: true}, 'cliente_usuario')),
    ),
  ], 'Criar convite', async (data, form) => {
    const invitation = await api(path, {method: 'POST', body: {email: data.get('email').trim(), role: data.get('role')}, signal});
    const index = result.invitations.findIndex((item) => item.id === invitation.id);
    if (index >= 0) result.invitations.splice(index, 1);
    result.invitations.unshift(invitation);
    renderInvitations();
    form.reset();
    return `Convite registrado. ${deliveryLabels[invitation.delivery] ?? 'Confira a lista de convites'}.`;
  }) : null;

  const temporaryForm = canEdit && canCreateWithTemporaryPassword ? actionForm([
    element('div', {class: 'form-grid'},
      inputField('E-mail', 'email', {type: 'email', maxlength: 254, required: true}),
      field('Perfil', select(roles, {name: 'role', required: true}, 'cliente_usuario')),
      inputField('Senha temporária', 'temporary_password', temporaryPasswordInput,
        'Use pelo menos 12 caracteres e até 72 bytes. Acentos e símbolos podem ocupar mais de um byte.'),
      inputField('Confirme a senha temporária', 'password_confirmation', temporaryPasswordInput),
    ),
  ], 'Cadastrar e vincular', async (data, form) => {
    const password = data.get('temporary_password');
    const confirmation = data.get('password_confirmation');
    form.elements.namedItem('temporary_password').value = '';
    form.elements.namedItem('password_confirmation').value = '';
    data.delete('temporary_password');
    data.delete('password_confirmation');
    if (password !== confirmation) throw new UserMessage('As duas senhas precisam ser iguais.');
    if ([...password].length < 12 || new TextEncoder().encode(password).byteLength > 72) {
      throw new UserMessage('A senha temporária precisa ter pelo menos 12 caracteres e no máximo 72 bytes.');
    }

    let member;
    try {
      member = await api(path + '/temporary', {
        method: 'POST',
        body: {email: data.get('email').trim(), role: data.get('role'), temporary_password: password},
        signal,
      });
    } catch (error) {
      if (error.status === 409) throw new UserMessage('Já existe uma conta ativa ou um cadastro incompatível. Confira os acessos pelo e-mail.');
      throw error;
    }
    const index = result.members.findIndex((item) => item.user_id === member.user_id);
    if (index >= 0) result.members.splice(index, 1);
    result.members.unshift(member);
    renderUsers();
    form.reset();

    try {
      const updated = await api(path, {signal});
      result.invitations = updated.invitations;
      renderInvitations();
    } catch (error) {
      if (signal?.aborted) return;
      return 'Usuário cadastrado e vinculado. Reabra esta empresa para atualizar a lista de convites.';
    }
    return 'Usuário cadastrado e vinculado à empresa.';
  }) : null;

  target.replaceChildren(
    panel('Usuários vinculados', memberList),
    temporaryForm ? panel('Cadastro por senha temporária',
      muted('Cadastre uma conta nova ou conclua um convite pendente desta empresa. Para uma conta pendente, a ação substitui a senha de entrada. Entregue a senha temporária por um canal privado.'),
      element('p', {class: 'muted'}, 'Para vincular uma conta já ativa, use ', link('/admin/acessos', 'Acessos por usuário'), '.'),
      temporaryForm,
    ) : null,
    panel('Convites', inviteForm,
      canEdit ? muted('Para reenviar um convite sem aceite que esteja pendente ou enviado, aguarde um minuto e informe o mesmo e-mail e perfil.') : null,
      inviteList,
    ),
  );
}
