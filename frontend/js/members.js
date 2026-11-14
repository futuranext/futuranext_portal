import {api, resource} from './client.js';
import {actionForm, checkField, dateTime, element, field, inputField, muted, panel, roleNames, select} from './ui.js';

const memberRoles = [
  ['cliente_usuario', roleNames.cliente_usuario],
  ['cliente_admin', roleNames.cliente_admin],
];
const deliveryLabels = {
  sent: 'Enviado por e-mail',
  existing_account: 'Disponível na conta da pessoa convidada',
  pending: 'Envio pendente',
};

export async function members(target, path, {canEdit, canAssignAdmin, signal}) {
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

  target.replaceChildren(
    panel('Usuários vinculados', memberList),
    panel('Convites', inviteForm,
      canEdit ? muted('Para reenviar um convite sem aceite que esteja pendente ou enviado, aguarde um minuto e informe o mesmo e-mail e perfil.') : null,
      inviteList,
    ),
  );
}
