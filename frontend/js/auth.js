import {auth, session} from './client.js';
import {actionForm, button, element, focusHeading, inputField, link, muted, notice, runAction, screen, statusMessage, UserMessage} from './ui.js';

const view = document.body.dataset.view;
const currentUrl = new URL(location.href);
const destination = currentUrl.searchParams.get('destino') === 'admin' ? '/admin' : '/portal';
const fragment = new URLSearchParams(currentUrl.hash.slice(1));
let otpHash = fragment.get('type') === view ? fragment.get('token_hash') : null;
const callbackNames = ['token_hash', 'code', 'access_token', 'refresh_token'];
const hasCallback = Boolean(currentUrl.hash) || callbackNames.some((name) => currentUrl.searchParams.has(name));
for (const name of callbackNames) currentUrl.searchParams.delete(name);
if (hasCallback) history.replaceState(null, '', `${currentUrl.pathname}${currentUrl.search}`);

const email = () => inputField('E-mail', 'email', {type: 'email', autocomplete: 'username', maxlength: 254, required: true});
const signInLink = () => link('/login', 'Voltar para entrar');

function loginScreen() {
  screen.replaceChildren(element('div', {class: 'stack'},
    muted('Use o e-mail cadastrado para acessar sua conta.'),
    actionForm([
      email(),
      inputField('Senha', 'password', {type: 'password', autocomplete: 'current-password', required: true}),
    ], 'Entrar', async (data) => {
      const {error} = await auth.signInWithPassword({email: data.get('email').trim(), password: data.get('password')});
      if (error) throw new UserMessage(error.status === 429 ? 'Muitas tentativas. Aguarde e tente novamente.' : 'Confira seu e-mail e sua senha e tente novamente.');
      location.replace(destination);
    }, {singleUse: true}),
    element('div', {class: 'account-links'}, link('/recuperar-senha', 'Esqueci minha senha')),
  ));
}

function recoveryRequest() {
  screen.replaceChildren(element('div', {class: 'stack'},
    muted('Informe seu e-mail para solicitar um link de recuperação.'),
    actionForm([email()], 'Enviar link', async (data) => {
      const {error} = await auth.resetPasswordForEmail(data.get('email').trim(), {redirectTo: new URL('/recuperar-senha', location.origin).href});
      if (error && error.status !== 400 && error.status !== 404) {
        throw new UserMessage(error.status === 429 ? 'Aguarde antes de solicitar outro link.' : 'Não foi possível solicitar o link. Tente novamente.');
      }
      return 'Se o e-mail estiver cadastrado, você receberá o link de recuperação.';
    }),
    signInLink(),
  ));
}

function newPassword(userId) {
  const parameters = {type: 'password', autocomplete: 'new-password', minlength: 12, required: true};
  const heading = element('h2', {}, 'Defina sua nova senha');
  screen.replaceChildren(element('div', {class: 'stack'}, heading,
    actionForm([
      inputField('Nova senha', 'password', parameters, 'Use pelo menos 12 caracteres.'),
      inputField('Confirme a senha', 'confirmation', parameters),
    ], 'Salvar senha', async (data) => {
      const password = data.get('password');
      if (password !== data.get('confirmation')) throw new UserMessage('As duas senhas precisam ser iguais.');
      if ((await session())?.user.id !== userId) throw new UserMessage('O link perdeu validade. Solicite um novo link.');
      const {error} = await auth.updateUser({password});
      if (error) {
        if (error.code === 'same_password') throw new UserMessage('Escolha uma senha diferente da atual.');
        if (error.code === 'weak_password') throw new UserMessage('Escolha uma senha mais forte.');
        throw new UserMessage('Não foi possível salvar a senha. Confira o link e tente novamente.');
      }
      if (view === 'invite') {
        location.replace('/portal');
        return;
      }
      const {error: signOutError} = await auth.signOut({scope: 'local'});
      if (signOutError) {
        screen.replaceChildren(notice('Senha atualizada. A sessão desta aba continua ativa.'), link('/portal', 'Ir para o portal'));
      } else {
        screen.replaceChildren(notice('Senha atualizada. Entre com a nova senha.'), signInLink());
      }
      focusHeading(screen);
    }, {singleUse: true}),
  ));
  focusHeading(screen);
}

function confirmation() {
  const status = statusMessage();
  const proceed = button(view === 'invite' ? 'Confirmar convite' : 'Confirmar recuperação');
  proceed.addEventListener('click', runAction(proceed, status, async () => {
    const {data, error} = await auth.verifyOtp({token_hash: otpHash, type: view});
    if (error || !data.session) throw new UserMessage('O link é inválido ou expirou. Solicite um novo link.');
    otpHash = null;
    newPassword(data.session.user.id);
  }, {singleUse: true}));
  screen.replaceChildren(element('div', {class: 'stack'},
    muted('Confirme para usar este link e definir sua senha.'), proceed, status, signInLink(),
  ));
}

if (!auth) {
  screen.replaceChildren(notice('O acesso à conta está aguardando configuração pela equipe da FuturaNext.'));
} else if (view === 'login') {
  loginScreen();
} else if (otpHash && otpHash.length <= 512) {
  confirmation();
} else if (view === 'recovery') {
  recoveryRequest();
  if (hasCallback) screen.prepend(notice('Este link é inválido. Solicite um novo link abaixo.', true));
} else {
  screen.replaceChildren(element('div', {class: 'stack'},
    notice('Abra o link do convite recebido por e-mail. Se já tem uma conta, entre para consultar seus convites.'), signInLink(),
  ));
}
