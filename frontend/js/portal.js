import {api, resource} from './client.js';
import {members} from './members.js';
import {requireMfa} from './mfa.js';
import {button, dateTime, definitionList, element, field, link, muted, notice, panel, roleNames, runAction, screen, select, statusMessage, UserMessage} from './ui.js';
import {boot, navigation, view} from './workspace.js';

const pages = [
  ['/portal', 'Visão geral', 'overview'],
  ['/portal/produtos', 'Ferramentas', 'products'],
  ['/portal/perfil', 'Meu perfil', 'profile'],
];

function invitations(items, accepted) {
  if (!items.length) return null;
  const list = element('ul', {class: 'record-list'}, items.map((item) => {
    const accept = button('Aceitar convite', true);
    accept.setAttribute('aria-label', `Aceitar convite de ${item.organization_name}`);
    const status = statusMessage();
    accept.addEventListener('click', runAction(accept, status, () => accepted(item.id), {singleUse: true}));
    return element('li', {class: 'record'},
      element('div', {class: 'stack compact'},
        element('strong', {}, item.organization_name),
        muted(`${roleNames[item.role] ?? item.role} · Validade ${dateTime(item.expires_at)}`), status,
      ),
      accept,
    );
  }));
  return panel('Convites pendentes', list);
}

function profile(target, context, tenant) {
  const details = [['E-mail', context.email ?? 'Indisponível']];
  if (tenant) details.push(['Empresa', tenant.name], ['Perfil na empresa', roleNames[tenant.role] ?? tenant.role]);
  if (context.platform_role) details.push(['Perfil FuturaNext', roleNames[context.platform_role] ?? context.platform_role]);
  const higherAssurance = context.aal === 'aal2';
  const security = panel('Segurança da conta',
    muted(higherAssurance ? 'Sessão verificada em duas etapas.' : 'Sessão autenticada por senha.'),
    link('/recuperar-senha', 'Alterar senha pelo e-mail'),
  );
  if (!higherAssurance) {
    const stepUp = button('Verificar em duas etapas', true);
    const mfaTarget = element('div', {class: 'stack'});
    const status = statusMessage();
    stepUp.addEventListener('click', runAction(stepUp, status, () => requireMfa(mfaTarget), {singleUse: true}));
    security.append(stepUp, status, mfaTarget);
  }
  target.replaceChildren(panel('Dados da conta', definitionList(details)), security);
}

const toolsPage = '/portal/produtos';

function portalNavigation(tenant, canQuote = false, selectedTool = null) {
  const entries = [...pages];
  if (canQuote) entries.splice(2, 0, [`${toolsPage}?ferramenta=cotacao-seguros`, 'Portal de cotações', 'quotes']);
  if (tenant?.role === 'cliente_admin') entries.push(['/portal/usuarios', 'Usuários', 'members']);
  navigation(entries, view === 'products' && selectedTool === 'cotacao-seguros' && canQuote ? 'quotes' : view);
}

function offerCard(offer, base, signal) {
  const status = statusMessage();
  const request = button('Solicitar acesso');
  request.setAttribute('aria-label', `Solicitar acesso a ${offer.name}`);
  request.addEventListener('click', runAction(request, status, async () => {
    await api(`${base}/offers/${resource(offer.id)}/request`, {method: 'POST', signal});
    request.hidden = true;
    return 'Solicitação enviada para análise.';
  }, {singleUse: true}));
  return element('article', {class: 'product-card'},
    element('h3', {}, offer.name), offer.description ? muted(offer.description) : null,
    offer.pending ? notice('Em análise') : element('div', {class: 'stack compact'}, request, status),
  );
}

async function scopedView(target, context, tenant, signal) {
  if (view === 'profile') {
    profile(target, context, tenant);
    return;
  }
  if (!tenant) {
    target.replaceChildren(notice(context.organizations.length ? 'Escolha uma empresa para continuar.' : 'Sua conta ainda não está vinculada a uma empresa ativa.'));
    if (context.platform_role === 'super_admin' && !context.organizations.length) {
      target.append(link('/admin/acessos', 'Configurar meu acesso a uma empresa'));
    }
    return;
  }

  const base = `/tenants/${resource(tenant.id)}`;
  if (view === 'members') {
    if (tenant.role !== 'cliente_admin') {
      target.replaceChildren(notice('Seu perfil nesta empresa não tem acesso à gestão de usuários.', true));
    } else if (context.aal !== 'aal2') {
      await requireMfa(target);
    } else {
      await members(target, `${base}/users`, {canEdit: true, canAssignAdmin: false, signal});
    }
    return;
  }

  const selectedTool = new URLSearchParams(location.search).get('ferramenta');
  const [products, offers] = await Promise.all([
    api(`${base}/products`, {signal}),
    view === 'products' && !selectedTool ? api(`${base}/offers`, {signal}) : [],
  ]);
  if (signal.aborted) return;
  portalNavigation(tenant, products.some(({slug}) => slug === 'cotacao-seguros'), selectedTool);
  if (view === 'products') {
    if (selectedTool) {
      const tool = products.find(({slug}) => slug === selectedTool);
      if (!tool) {
        target.replaceChildren(notice('Esta ferramenta não está disponível para a empresa selecionada.'), link(toolsPage, 'Ver ferramentas'));
        return;
      }
      const {openTool} = await import('./tools/index.js');
      if (signal.aborted) return;
      await openTool(target, tool, {tenant, signal, canEdit: tenant.role === 'cliente_admin', aal: context.aal});
      return;
    }
    const content = products.length ? element('div', {class: 'product-grid'}, products.map((product) => (
      element('article', {class: 'product-card'}, element('h3', {}, product.name),
        product.description ? muted(product.description) : null,
        link(`${toolsPage}?ferramenta=${resource(product.slug)}`, `Abrir ${product.name}`, 'button secondary'),
      )
    ))) : muted('Nenhuma ferramenta liberada para seu perfil nesta empresa.');
    target.replaceChildren(panel('Ferramentas liberadas', content));
    if (offers.length) target.append(panel('Adquirir ferramentas',
      element('div', {class: 'product-grid'}, offers.map((offer) => offerCard(offer, base, signal)))));
  } else {
    target.replaceChildren(panel(tenant.name,
      definitionList([['Seu perfil', roleNames[tenant.role] ?? tenant.role], ['Ferramentas liberadas', String(products.length)]]),
      link(toolsPage, 'Ver ferramentas'),
    ));
  }
}

void boot('portal', (initialContext) => {
  let context = initialContext;
  const key = `futuranext-organization:${context.user_id}`;
  let request;
  let invitationRevision = 0;

  function draw(message) {
    request?.abort();
    const saved = sessionStorage.getItem(key);
    const tenant = context.organizations.find((org) => org.id === saved)
      ?? (context.organizations.length === 1 ? context.organizations[0] : null);
    portalNavigation(tenant);

    const viewTarget = element('div', {class: 'stack'}, notice('Carregando…'));
    const invitationList = invitations(context.invitations, async (id) => {
      await api(`/auth/invitations/${resource(id)}/accept`, {method: 'POST'});
      const revision = ++invitationRevision;
      const refreshed = await api('/auth/context');
      if (revision === invitationRevision) {
        context = refreshed;
        draw('Convite aceito. O vínculo com a empresa foi atualizado.');
      }
    });

    let picker;
    if (context.organizations.length) {
      const options = [['', 'Selecione uma empresa'], ...context.organizations.map((org) => [org.id, org.name])];
      const control = select(options, {}, tenant?.id ?? '');
      control.options[0].disabled = true;
      control.addEventListener('change', () => {
        sessionStorage.setItem(key, control.value);
        if (view === 'products' && new URLSearchParams(location.search).has('ferramenta')) {
          history.replaceState(null, '', toolsPage);
        }
        draw();
        screen.querySelector('.tenant-picker select')?.focus();
      });
      picker = element('div', {class: 'tenant-picker'}, field('Empresa', control));
    } else {
      sessionStorage.removeItem(key);
    }

    screen.replaceChildren(...[picker, message && notice(message), invitationList, viewTarget].filter(Boolean));
    request = new AbortController();
    const {signal} = request;
    void scopedView(viewTarget, context, tenant, signal).catch((error) => {
      if (!signal.aborted) viewTarget.replaceChildren(notice(error instanceof UserMessage ? error.message : 'Não foi possível carregar esta tela. Tente novamente.', true));
    });
  }

  draw();
});
