import {api, resource} from './client.js';
import {userRemoval, userRemovedMessage} from './remove-user.js';
import {actionForm, checkField, combobox, dateTime, element, field, focusHeading, link, muted, notice, panel, roleNames, screen, select} from './ui.js';

const clientRoles = ['cliente_usuario', 'cliente_admin'].map((role) => [role, roleNames[role]]);
const outcomes = {pending: 'Em análise', approved: 'Aprovada', rejected: 'Recusada'};

export function userAccess(currentUserId) {
  const selected = element('div', {class: 'stack'});
  let loading;
  async function showAccess(account, signal, onRemoved) {
    const root = `/admin/access/users/${resource(account.user_id)}`;
    const companies = await api(root, {signal});
    if (signal.aborted) return;
    const cards = companies.map((company) => {
      const summary = element('summary');
      const updateTitle = () => {
        const status = company.granted ? roleNames[company.role] : 'Sem acesso';
        summary.textContent = `${company.name} · ${status}${company.company_active ? '' : ' · Empresa inativa'}`;
      };
      updateTitle();
      const form = actionForm([
        checkField('Liberar acesso a esta empresa', 'granted', company.granted),
        field('Perfil nesta empresa', select(clientRoles, {name: 'role'}, company.role)),
      ], 'Salvar acesso', async (data) => {
        const body = {granted: data.has('granted'), role: data.get('role')};
        await api(`${root}/clients/${resource(company.tenant_id)}`, {method: 'PUT', body, signal});
        Object.assign(company, body);
        updateTitle();
        return 'Acesso atualizado.';
      });
      return element('details', {class: 'edit-disclosure'}, summary, form);
    });
    selected.replaceChildren(panel(account.email, ...cards,
      cards.length ? null : muted('Nenhuma empresa disponível para vincular a esta conta.'),
      userRemoval(account, {currentUserId, signal, onRemoved})));
  }
  const accounts = new Map();
  const control = select([['', 'Selecione uma conta']]);
  control.options[0].disabled = true;
  async function choose() {
    loading?.abort();
    loading = new AbortController();
    const {signal} = loading;
    const account = accounts.get(control.value);
    if (!account) return;
    selected.replaceChildren(notice('Carregando acessos…'));
    try {
      await showAccess(account, signal, () => {
        Array.from(control.options).find(({value}) => value === account.user_id)?.remove();
        accounts.delete(account.user_id);
        control.value = '';
        selected.replaceChildren(notice(userRemovedMessage), link('/admin/usuarios', 'Cadastrar novo usuário'));
        focusHeading(selected);
      });
    } catch (error) {
      if (!signal.aborted) selected.replaceChildren(notice(error instanceof Error && error.message || 'Não foi possível buscar os acessos.', true));
    }
  }
  control.addEventListener('change', () => void choose());
  const search = field('E-mail da conta', combobox(control, {
    placeholder: 'Digite parte do e-mail',
    source: async (term) => {
      const found = await api(`/admin/access/users?q=${resource(term)}`);
      for (const account of found) accounts.set(account.user_id, account);
      return found.map(({user_id, email}) => [user_id, email]);
    },
  }), 'As contas aparecem enquanto você digita. Não encontrou? Convide em Usuários.');
  screen.replaceChildren(panel('Selecionar usuário', search), selected);
}

export async function toolRequests() {
  const path = '/admin/tool-requests';
  const requests = await api(path);
  const cards = requests.map((item) => {
    const status = muted(outcomes[item.status]);
    const decision = item.status === 'pending' ? actionForm([
      field('Decisão', select([['approved', 'Aprovar e liberar licença'], ['rejected', 'Recusar']], {name: 'status'}, 'approved')),
    ], 'Registrar decisão', async (data, form) => {
      const outcome = data.get('status');
      await api(`${path}/${resource(item.id)}`, {method: 'PUT', body: {status: outcome}});
      status.textContent = outcomes[outcome];
      for (const control of form.elements) control.disabled = true;
      return 'Decisão registrada.';
    }, {singleUse: true}) : null;
    return panel(`${item.product} · ${item.organization}`, muted(dateTime(item.created_at)), status, decision);
  });
  screen.replaceChildren(...(cards.length ? cards : [notice('Nenhuma solicitação de ferramenta.')]));
}
