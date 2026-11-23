import {auth, publicApi, session} from './client.js';
import {requireMfa} from './mfa.js';
import {actionForm, button, element, focusHeading, inputField, link, muted, notice, runAction, screen, statusMessage, UserMessage} from './ui.js';
import {RETURN_KEY} from './workspace.js';

const view = document.body.dataset.view;
const currentUrl = new URL(location.href);
const area = currentUrl.searchParams.get('destino') === 'admin' ? '/admin' : '/portal';

// Volta ao endereço guardado antes do login, desde que seja da mesma área e da mesma origem.
function destination() {
  let saved = null;
  try {
    saved = sessionStorage.getItem(RETURN_KEY);
    sessionStorage.removeItem(RETURN_KEY);
  } catch { /* sem armazenamento */ }
  if (!saved || !saved.startsWith('/') || saved.startsWith('//')) return area;
  try {
    const target = new URL(saved, location.origin);
    const inArea = target.pathname === area || target.pathname.startsWith(`${area}/`);
    return target.origin === location.origin && inArea ? target.pathname + target.search + target.hash : area;
  } catch {
    return area;
  }
}
const fragment = new URLSearchParams(currentUrl.hash.slice(1));
const otpParameters = fragment.has('token_hash') ? fragment : currentUrl.searchParams;
let otpHash = otpParameters.get('type') === view ? otpParameters.get('token_hash') : null;
let recoveryCode = view === 'recovery' && !currentUrl.hash ? currentUrl.searchParams.get('code') : null;
const callbackError = fragment.get('error_code') ?? currentUrl.searchParams.get('error_code');
const callbackNames = ['token_hash', 'code', 'access_token', 'refresh_token', 'error', 'error_code', 'error_description'];
const hasCallback = callbackNames.some((name) => fragment.has(name) || currentUrl.searchParams.has(name));
for (const name of callbackNames) currentUrl.searchParams.delete(name);
if (hasCallback) {
  currentUrl.searchParams.delete('type');
  history.replaceState(null, '', `${currentUrl.pathname}${currentUrl.search}`);
}

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
      location.replace(destination());
    }, {singleUse: true}),
    element('div', {class: 'account-links'}, link('/recuperar-senha', 'Esqueci minha senha'),
      link('/cadastro', 'Ainda não tem conta? Teste grátis por 15 dias')),
  ));
}

function signupDone(result) {
  const messages = {
    sent: `Pronto! Enviamos um e-mail para ${result.email}. Abra o link para criar sua senha e acessar o portal. Seu teste grátis de ${result.trial_days} dias já começou.`,
    existing_account: `Você já tem uma conta com ${result.email}. Entre na Área do Cliente e aceite o convite da nova empresa para começar o teste grátis.`,
    pending: 'Cadastro recebido! Não conseguimos enviar o e-mail de acesso agora; a equipe da FuturaNext vai enviar o link em breve.',
  };
  const heading = element('h2', {}, 'Cadastro concluído');
  screen.replaceChildren(element('div', {class: 'stack'}, heading, notice(messages[result.delivery] ?? messages.pending),
    signInLink()));
  focusHeading(screen);
}

function signupScreen() {
  const terms = element('label', {class: 'check'},
    element('input', {type: 'checkbox', name: 'accept_terms', required: true}),
    'Aceito as condições do teste grátis: 15 dias sem custo, um teste por CPF/CNPJ, e as ferramentas param ao final do período se não forem contratadas. Autorizo o contato da FuturaNext sobre a plataforma.');
  screen.replaceChildren(element('div', {class: 'stack'},
    muted('Teste a plataforma FuturaNext por 15 dias, sem cartão de crédito. Você recebe por e-mail o link para criar sua senha.'),
    actionForm([
      inputField('Seu nome', 'full_name', {autocomplete: 'name', minlength: 2, maxlength: 100, required: true}),
      email(),
      inputField('Nome da empresa', 'company', {autocomplete: 'organization', minlength: 2, maxlength: 120, required: true}),
      inputField('CPF ou CNPJ', 'document', {inputmode: 'numeric', minlength: 11, maxlength: 24, required: true}),
      inputField('Telefone com DDD', 'phone', {type: 'tel', autocomplete: 'tel', minlength: 10, maxlength: 24, required: true}),
      terms,
    ], 'Começar teste grátis', async (data) => {
      const result = await publicApi('/signup', {
        full_name: data.get('full_name').trim(), email: data.get('email').trim().toLowerCase(),
        company: data.get('company').trim(), document: data.get('document').trim(),
        phone: data.get('phone').trim(), accept_terms: data.has('accept_terms'),
      });
      signupDone(result);
    }, {singleUse: true}),
    element('div', {class: 'account-links'}, link('/login', 'Já tenho conta')),
  ));
}

function recoveryRequest() {
  screen.replaceChildren(element('div', {class: 'stack'},
    muted('Informe seu e-mail para solicitar um link de recuperação. Abra o link novo na mesma aba em que fez o pedido.'),
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
        if (error.code === 'insufficient_aal' && view === 'recovery') {
          await verifyRecoveryFactor(userId);
          return;
        }
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

async function verifyRecoveryFactor(userId) {
  if ((await session())?.user.id !== userId) {
    throw new UserMessage('O link perdeu validade. Solicite um novo link.');
  }
  await requireMfa(screen, {
    allowEnrollment: false,
    onVerified: () => newPassword(userId),
  });
  focusHeading(screen);
}

function confirmation() {
  const status = statusMessage();
  const proceed = button(view === 'invite' ? 'Confirmar convite' : 'Confirmar recuperação');
  proceed.addEventListener('click', runAction(proceed, status, async () => {
    const usedCode = Boolean(recoveryCode) && !otpHash;
    const {data, error} = usedCode
      ? await auth.exchangeCodeForSession(recoveryCode)
      : await auth.verifyOtp({token_hash: otpHash, type: view});
    if (error || !data.session || (usedCode && data.redirectType !== 'recovery')) {
      if (usedCode && data.session) await auth.signOut({scope: 'local'});
      throw new UserMessage(usedCode
        ? 'Abra este link na aba em que pediu a recuperação ou solicite um novo link.'
        : 'O link é inválido ou expirou. Solicite um novo link.');
    }
    otpHash = null;
    recoveryCode = null;
    const userId = data.session.user.id;
    if (view === 'recovery') {
      const {data: levels} = await auth.mfa.getAuthenticatorAssuranceLevel();
      if (levels?.nextLevel === 'aal2' && levels.currentLevel !== 'aal2') {
        await verifyRecoveryFactor(userId);
        return;
      }
    }
    newPassword(userId);
  }, {singleUse: true}));
  screen.replaceChildren(element('div', {class: 'stack'},
    muted('Confirme para usar este link e definir sua senha.'), proceed, status,
    view === 'recovery' ? link('/recuperar-senha', 'Solicitar outro link') : signInLink(),
  ));
}

if (!auth) {
  screen.replaceChildren(notice('O acesso à conta está aguardando configuração pela equipe da FuturaNext.'));
} else if (view === 'login') {
  loginScreen();
} else if (view === 'signup') {
  signupScreen();
} else if ((otpHash && otpHash.length <= 512) || (recoveryCode && recoveryCode.length <= 512)) {
  confirmation();
} else if (view === 'recovery') {
  recoveryRequest();
  if (hasCallback) {
    const message = ['otp_expired', 'flow_state_expired', 'flow_state_not_found'].includes(callbackError)
      ? 'Este link expirou ou já foi usado. Solicite um link novo abaixo.'
      : 'Não foi possível confirmar o link recebido. Solicite um link novo abaixo.';
    screen.prepend(notice(message, true));
  }
} else {
  screen.replaceChildren(element('div', {class: 'stack'},
    notice('Abra o link do convite recebido por e-mail. Se já tem uma conta, entre para consultar seus convites.'), signInLink(),
  ));
}
