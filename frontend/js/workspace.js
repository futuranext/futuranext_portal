import {api, ApiError, auth, session} from './client.js';
import {button, element, link, notice, runAction, screen, statusMessage, UserMessage} from './ui.js';

export const view = document.body.dataset.view;
export const isStaff = (context) => ['super_admin', 'suporte'].includes(context.platform_role);

export function navigation(entries, active = view) {
  const nav = document.getElementById('navigation');
  nav.replaceChildren(...entries.map(([path, label, id]) => (
    element('a', {href: path, 'aria-current': active === id ? 'page' : null}, label)
  )));
}

function header(context, area) {
  const headerActions = document.getElementById('header-actions');
  const status = statusMessage();
  const logout = button('Sair', true);
  logout.addEventListener('click', runAction(logout, status, async () => {
    const {error} = await auth.signOut({scope: 'local'});
    if (error) throw new UserMessage('Não foi possível encerrar a sessão. Tente novamente.');
    location.replace('/login');
  }, {singleUse: true}));

  const shortcut = area === 'admin'
    ? link('/portal', 'Portal do cliente')
    : isStaff(context) && link('/admin', 'Administração');
  headerActions.replaceChildren(...[shortcut, logout, status].filter(Boolean));
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
      location.replace(login);
      return;
    }
    auth.onAuthStateChange((event, current) => {
      if (event === 'SIGNED_OUT' || event === 'TOKEN_REFRESHED' && !current) location.replace(login);
    });
    const context = await api('/auth/context');
    header(context, area);
    await render(context);
  } catch (error) {
    const message = error instanceof UserMessage ? error.message : 'Não foi possível carregar esta tela. Tente novamente.';
    screen.replaceChildren(notice(message, true));
    if (error instanceof ApiError && error.status === 401) screen.append(link(login, 'Entrar novamente'));
  }
}
