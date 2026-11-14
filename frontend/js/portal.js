import {api, resource} from './client.js';
import {members} from './members.js';
import {requireMfa} from './mfa.js';
import {button, dateTime, definitionList, element, field, link, muted, notice, panel, roleNames, runAction, screen, select, statusMessage, UserMessage} from './ui.js';
import {boot, navigation, view} from './workspace.js';

const pages = [
  ['/portal', 'Visão geral', 'overview'],
  ['/portal/produtos', 'Produtos', 'products'],
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

async function scopedView(target, context, tenant, signal) {
  if (view === 'profile') {
    profile(target, context, tenant);
    return;
  }
  if (!tenant) {
    target.replaceChildren(notice(context.organizations.length ? 'Escolha uma empresa para continuar.' : 'Sua conta ainda não está vinculada a uma empresa ativa.'));
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

  const products = await api(`${base}/products`, {signal});
  if (signal.aborted) return;
  if (view === 'products') {
    const content = products.length ? element('div', {class: 'product-grid'}, products.map((product) => (
      element('article', {class: 'product-card'}, element('h3', {}, product.name), product.description && muted(product.description))
    ))) : muted('Nenhum produto disponível para seu perfil nesta empresa.');
    target.replaceChildren(panel('Produtos disponíveis', content,
      products.length ? muted('Os módulos operacionais estão em preparação.') : null,
    ));
  } else {
    target.replaceChildren(panel(tenant.name,
      definitionList([['Seu perfil', roleNames[tenant.role] ?? tenant.role], ['Produtos disponíveis', String(products.length)]]),
      link('/portal/produtos', 'Ver produtos'),
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
    navigation(tenant?.role === 'cliente_admin' ? [...pages, ['/portal/usuarios', 'Usuários', 'members']] : pages);

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
