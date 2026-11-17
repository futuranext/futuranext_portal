import {api, resource} from './client.js';
import {actionForm, checkField, dateTime, element, field, inputField, link, muted, notice, panel, roleNames, screen, select} from './ui.js';

const clientRoles = ['cliente_usuario', 'cliente_admin'].map((role) => [role, roleNames[role]]);
const outcomes = {pending: 'Em análise', approved: 'Aprovada', rejected: 'Recusada'};

export function userAccess() {
  const results = element('div', {class: 'stack'});
  const selected = element('div', {class: 'stack'});
  let loading;
  async function showAccess(account, signal) {
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
      cards.length ? null : muted('Nenhuma empresa cadastrada para associar.')));
  }
  const search = actionForm([
    inputField('E-mail da conta', 'q', {type: 'search', required: true, minlength: 2, maxlength: 254, autocomplete: 'off'}),
  ], 'Buscar contas', async (data) => {
    loading?.abort();
    selected.replaceChildren();
    results.replaceChildren();
    const {q} = Object.fromEntries(data);
    const accounts = await api(`/admin/access/users?q=${resource(q.trim())}`);
    if (!accounts.length) {
      results.replaceChildren(notice('Nenhuma conta confirmada encontrada.'), link('/admin/usuarios', 'Convidar novo usuário'));
      return;
    }
    const options = [['', 'Selecione uma conta'], ...accounts.map(({user_id, email}) => [user_id, email])];
    const control = select(options);
    control.options[0].disabled = true;
    async function choose() {
      loading?.abort();
      loading = new AbortController();
      const {signal} = loading;
      const account = accounts.find(({user_id}) => user_id === control.value);
      if (!account) return;
      selected.replaceChildren(notice('Carregando acessos…'));
      try {
        await showAccess(account, signal);
      } catch (error) {
        if (!signal.aborted) selected.replaceChildren(notice(error instanceof Error && error.message || 'Não foi possível buscar os acessos.', true));
      }
    }
    control.addEventListener('change', () => void choose());
    results.replaceChildren(field('Conta', control));
    if (accounts.length === 1) {
      control.value = accounts[0].user_id;
      await choose();
    }
  });
  screen.replaceChildren(panel('Selecionar usuário', search, results), selected);
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
