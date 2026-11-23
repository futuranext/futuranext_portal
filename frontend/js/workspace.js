import {api, ApiError, auth, session} from './client.js';
import {requireFirstPassword} from './first-password.js';
import {hasVerifiedSession, requireMfa} from './mfa.js';
import {button, element, link, notice, runAction, screen, statusMessage, UserMessage} from './ui.js';

export const view = document.body.dataset.view;
export const RETURN_KEY = 'futuranext-return';
export const isStaff = (context) => ['super_admin', 'suporte'].includes(context.platform_role);

export function navigation(entries, active = view) {
  const nav = document.getElementById('navigation');
  nav.replaceChildren(...entries.map(([path, label, id]) => (
    element('a', {href: path, 'aria-current': active === id ? 'page' : null}, label)
  )));
}

// Nome exibido no topo: o nome informado no perfil ou, na falta dele, o e-mail da conta.
export function displayName(user) {
  const name = typeof user?.user_metadata?.full_name === 'string' ? user.user_metadata.full_name.trim() : '';
  return name || user?.email || '';
}

export function showUser(name) {
  const target = document.getElementById('header-user');
  if (target) target.textContent = name ? `Olá, ${name.split(/\s+/)[0].includes('@') ? name : name.split(/\s+/)[0]}` : '';
}

function header(context, area, login, user = null) {
  const headerActions = document.getElementById('header-actions');
  const status = statusMessage();
  const logout = button('Sair', true);
  logout.addEventListener('click', runAction(logout, status, async () => {
    const {error} = await auth.signOut({scope: 'local'});
    if (error) throw new UserMessage('Não foi possível encerrar a sessão. Tente novamente.');
    location.replace(login);
  }, {singleUse: true}));

  const shortcut = context && (area === 'admin'
    ? link('/portal', 'Portal do cliente')
    : isStaff(context) && link('/admin', 'Administração'));
  const greeting = context && user ? element('span', {class: 'header-user', id: 'header-user', title: user.email ?? ''}) : null;
  headerActions.replaceChildren(...[greeting, shortcut, logout, status].filter(Boolean));
  if (greeting) showUser(displayName(user));
}

export async function boot(area, render) {
  const login = area === 'admin' ? '/login?destino=admin' : '/login';
  if (!auth) {
    screen.replaceChildren(notice('O acesso à conta está aguardando configuração pela equipe da FuturaNext.'));
    return;
  }

  window.addEventListener('pageshow', (event) => {
    if (event.persisted) location.reload();
  });
  window.addEventListener('pagehide', (event) => {
    if (event.persisted) screen.replaceChildren(notice('Carregando…'));
  });

  try {
    if (!await session()) {
      // A sessão vale por aba: links de e-mail (ex.: aceite do WhatsApp) abrem em aba nova.
      // Guarda o endereço completo, inclusive o #fragmento, para voltar a ele depois do login.
      try {
        sessionStorage.setItem(RETURN_KEY, location.pathname + location.search + location.hash);
      } catch { /* sem armazenamento: volta para a página inicial da área */ }
      location.replace(login);
      return;
    }
    auth.onAuthStateChange((event, current) => {
      if (event === 'SIGNED_OUT' || event === 'TOKEN_REFRESHED' && !current) location.replace(login);
    });

    header(null, area, login);
    if (await requireFirstPassword(screen, login)) return;

    if (!await hasVerifiedSession()) {
      await requireMfa(screen);
      return;
    }

    const context = await api('/auth/context');
    if (context.aal !== 'aal2') {
      await requireMfa(screen);
      return;
    }
    header(context, area, login, (await session())?.user);
    await render(context);
  } catch (error) {
    const message = error instanceof UserMessage ? error.message : 'Não foi possível carregar esta tela. Tente novamente.';
    screen.replaceChildren(notice(message, true));
    if (error instanceof ApiError && error.status === 401) screen.append(link(login, 'Entrar novamente'));
  }
}
