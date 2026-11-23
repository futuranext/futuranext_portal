import {api, auth, resource, session} from './client.js';
import {requireMfa} from './mfa.js';
import {actionForm, button, combobox, dateTime, definitionList, element, field, inputField, link, muted, notice, panel, roleNames, runAction, screen, select, statusMessage, UserMessage} from './ui.js';
import {boot, displayName, navigation, showUser, view} from './workspace.js';

const pages = [
  ['/portal', 'Visão geral', 'overview'],
  ['/portal/produtos', 'Ferramentas', 'products'],
  ['/portal/pagamento', 'Pagamento', 'billing'],
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

async function nameForm() {
  const current = (await session())?.user;
  const saved = current?.user_metadata?.full_name ?? '';
  return actionForm([
    inputField('Nome completo', 'full_name', {maxlength: 100, autocomplete: 'name', required: true, value: saved}),
  ], 'Salvar nome', async (data) => {
    const fullName = String(data.get('full_name')).trim().replace(/\s+/g, ' ');
    if (fullName.length < 2) throw new UserMessage('Informe seu nome.');
    const {data: updated, error} = await auth.updateUser({data: {full_name: fullName}});
    if (error) throw new UserMessage('Não foi possível salvar o nome. Tente novamente.');
    showUser(displayName(updated.user));
    return 'Nome atualizado.';
  });
}

async function profile(target, context, tenant) {
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
  target.replaceChildren(panel('Dados da conta', definitionList(details), await nameForm()), security);
}

const toolsPage = '/portal/produtos';

function portalNavigation(tenant, canQuote = false, selectedTool = null) {
  const entries = [...pages];
  if (canQuote) entries.splice(2, 0, [`${toolsPage}?ferramenta=cotacao-seguros`, 'Portal de cotações', 'quotes']);
  navigation(entries, view === 'products' && selectedTool === 'cotacao-seguros' && canQuote ? 'quotes' : view);
}

const brl = new Intl.NumberFormat('pt-BR', {style: 'currency', currency: 'BRL'});
const price = (cents) => `${brl.format(cents / 100)} por mês`;
const day = new Intl.DateTimeFormat('pt-BR', {dateStyle: 'short', timeZone: 'America/Sao_Paulo'});

function offerCard(offer, base, signal, isAdmin, refresh) {
  const status = statusMessage();
  const parts = [element('h3', {}, offer.name), offer.description ? muted(offer.description) : null];
  if (offer.price_cents) parts.push(element('p', {class: 'offer-price'}, price(offer.price_cents)));

  if (offer.price_cents && isAdmin) {
    const buy = button(`Contratar ${offer.name}`);
    buy.addEventListener('click', runAction(buy, status, async () => {
      if (!window.confirm(`Contratar ${offer.name} por ${price(offer.price_cents)}? O valor entra na mensalidade da empresa e a ferramenta é liberada agora.`)) return null;
      await api(`${base}/offers/${resource(offer.id)}/purchase`, {method: 'POST', signal});
      if (!signal.aborted) await refresh(`${offer.name} contratada. A ferramenta já está liberada e o valor entra na sua mensalidade.`);
      return null;
    }, {singleUse: true}));
    parts.push(element('div', {class: 'stack compact'}, buy, status));
    return element('article', {class: 'product-card'}, parts);
  }

  if (offer.pending) {
    parts.push(notice('Em análise'));
  } else {
    const request = button(offer.price_cents ? 'Pedir ao administrador' : 'Solicitar acesso', !!offer.price_cents);
    request.setAttribute('aria-label', `Solicitar acesso a ${offer.name}`);
    request.addEventListener('click', runAction(request, status, async () => {
      await api(`${base}/offers/${resource(offer.id)}/request`, {method: 'POST', signal});
      request.hidden = true;
      return 'Solicitação enviada.';
    }, {singleUse: true}));
    if (offer.price_cents) parts.push(muted('A contratação é feita pelo administrador da empresa.'));
    parts.push(element('div', {class: 'stack compact'}, request, status));
  }
  return element('article', {class: 'product-card'}, parts);
}

function planNotice(plan) {
  if (plan.trial_active) {
    return notice(`Teste grátis até ${day.format(new Date(plan.trial_ends_at))}. Contrate as ferramentas que quiser manter em "Contratar ferramentas"; a primeira mensalidade vence só no fim do teste.`);
  }
  if (plan.trial_ends_at && !plan.subscription) {
    return notice('Seu teste grátis terminou. Contrate as ferramentas em "Contratar ferramentas" para voltar a usá-las.', true);
  }
  return null;
}

async function scopedView(target, context, tenant, signal, message = null) {
  if (view === 'profile') {
    await profile(target, context, tenant);
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
  if (view === 'billing') {
    const {renderBillingPortal} = await import('./billing-portal.js');
    if (!signal.aborted) await renderBillingPortal(target, {tenant, signal});
    return;
  }
  if (view === 'members') {
    target.replaceChildren(notice('A gestão de usuários da empresa é feita pela equipe FuturaNext. Fale com suporte@futuranext.com.br para incluir ou remover acessos.'));
    return;
  }

  const selectedTool = new URLSearchParams(location.search).get('ferramenta');
  const [products, offers, plan] = await Promise.all([
    api(`${base}/products`, {signal}),
    view === 'products' && !selectedTool ? api(`${base}/offers`, {signal}) : [],
    selectedTool ? null : api(`${base}/plan`, {signal}).catch(() => null),
  ]);
  const banner = plan ? planNotice(plan) : null;
  const refresh = (text) => scopedView(target, context, tenant, signal, text);
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
    target.replaceChildren(...[message ? notice(message) : null, banner, panel('Ferramentas liberadas', content)].filter(Boolean));
    if (offers.length) target.append(panel('Contratar ferramentas',
      element('div', {class: 'product-grid'}, offers.map((offer) => offerCard(offer, base, signal, tenant.role === 'cliente_admin', refresh)))));
  } else {
    target.replaceChildren(...[banner].filter(Boolean), panel(tenant.name,
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

  // Links enviados por e-mail (ex.: aceite do WhatsApp) indicam a empresa em ?empresa=.
  const url = new URL(location.href);
  const requested = url.searchParams.get('empresa');
  if (requested) {
    if (context.organizations.some((org) => org.id === requested)) sessionStorage.setItem(key, requested);
    url.searchParams.delete('empresa');
    history.replaceState(null, '', url.pathname + url.search + url.hash);
  }

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
        screen.querySelector('.tenant-picker input, .tenant-picker select')?.focus();
      });
      picker = element('div', {class: 'tenant-picker'}, field('Empresa', context.organizations.length > 1
        ? combobox(control, {placeholder: 'Digite o nome da empresa'}) : control));
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
