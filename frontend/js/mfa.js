import {auth} from './client.js';
import {
  actionForm, button, element, field, focusHeading, inputField, muted, notice, panel,
  runAction, select, statusMessage, UserMessage,
} from './ui.js';

const defaultContinue = () => location.reload();
const support = () => element('p', {class: 'muted'},
  'Se perdeu acesso ao autenticador, fale com ',
  element('a', {href: 'mailto:suporte@futuranext.com.br'}, 'suporte@futuranext.com.br'), '.',
);

export async function hasVerifiedSession() {
  const {data, error} = await auth.mfa.getAuthenticatorAssuranceLevel();
  if (error) throw new UserMessage('Não foi possível verificar o nível de segurança da sessão. Tente entrar novamente.');
  return data.currentLevel === 'aal2';
}

function verifyForm(factors, onVerified) {
  const chosen = factors[0].id;
  const options = factors.map((factor, index) => [factor.id, factor.friendly_name || `Autenticador ${index + 1}`]);
  const chooseFactor = factors.length > 1
    ? field('Autenticador', select(options, {name: 'factor'}, chosen))
    : null;

  return actionForm([
    chooseFactor,
    inputField('Código do autenticador', 'code', {
      type: 'text', inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: 6, pattern: '[0-9]{6}', required: true,
    }),
  ], 'Verificar código', async (data) => {
    const {error} = await auth.mfa.challengeAndVerify({factorId: data.get('factor') ?? chosen, code: data.get('code')});
    if (error?.status === 429) throw new UserMessage('Muitas tentativas. Aguarde antes de pedir outro código.');
    if (error) throw new UserMessage('O código não foi aceito. Use o código atual do seu aplicativo.');
    if (!await hasVerifiedSession()) throw new UserMessage('A sessão não foi atualizada. Saia e entre novamente.');
    await onVerified();
  }, {singleUse: true});
}

const unfinished = (factors) => factors.all.filter(({factor_type: type, status}) => type === 'totp' && status === 'unverified');

function showChallenge(target, factors, onVerified) {
  target.replaceChildren(panel('Verificação em duas etapas',
    muted('Informe o código de seis dígitos do seu aplicativo autenticador.'), verifyForm(factors, onVerified), support(),
  ));
  focusHeading(target);
}

function showEnrollment(target, enrollment, onVerified) {
  const {qr_code: qrCode, secret} = enrollment.totp;
  const image = /^data:image\/svg\+xml[;,]/.test(qrCode)
    ? element('img', {class: 'mfa-qr', src: qrCode, alt: 'Código QR para cadastrar o autenticador'})
    : null;
  target.replaceChildren(panel('Adicionar autenticador',
    muted('Leia o código QR no seu aplicativo autenticador ou insira a chave manualmente. Depois, digite o código gerado.'),
    image,
    element('details', {}, element('summary', {}, 'Mostrar chave para inserção manual'), element('code', {class: 'mfa-secret'}, secret)),
    verifyForm([enrollment], onVerified),
  ));
  focusHeading(target);
}

export async function requireMfa(target, {onVerified = defaultContinue, allowEnrollment = true} = {}) {
  const {data, error} = await auth.mfa.listFactors();
  if (error) {
    const retry = button('Tentar novamente', true);
    const status = statusMessage();
    retry.addEventListener('click', runAction(retry, status, () => requireMfa(target, {onVerified, allowEnrollment})));
    target.replaceChildren(panel('Verificação em duas etapas',
      notice('Não foi possível consultar seus autenticadores.', true), retry, status, support(),
    ));
    focusHeading(target);
    return;
  }
  if (data.totp.length) {
    showChallenge(target, data.totp, onVerified);
    return;
  }
  if (!allowEnrollment) {
    target.replaceChildren(panel('Verificação em duas etapas',
      notice('Para continuar, sua conta precisa de um autenticador já verificado.', true), support(),
    ));
    focusHeading(target);
    return;
  }

  const pending = unfinished(data);
  const enroll = button(pending.length ? 'Gerar nova configuração' : 'Configurar autenticador');
  const status = statusMessage();
  enroll.addEventListener('click', runAction(enroll, status, async () => {
    const {data: latest, error: loadFailure} = await auth.mfa.listFactors();
    if (loadFailure) throw new UserMessage('Não foi possível consultar seus autenticadores. Tente novamente.');
    if (latest.totp.length) {
      showChallenge(target, latest.totp, onVerified);
      return;
    }
    for (const factor of unfinished(latest)) {
      const {error: removeFailure} = await auth.mfa.unenroll({factorId: factor.id});
      if (removeFailure && removeFailure.code !== 'mfa_factor_not_found') {
        throw new UserMessage('Não foi possível substituir a configuração pendente. Tente novamente.');
      }
    }
    const {data: enrollment, error: failure} = await auth.mfa.enroll({factorType: 'totp', issuer: 'FuturaNext'});
    if (failure || !enrollment?.totp) throw new UserMessage('Não foi possível iniciar a configuração. Tente novamente.');
    showEnrollment(target, enrollment, onVerified);
  }, {singleUse: true}));

  const resume = pending.length ? element('details', {},
    element('summary', {}, 'Já adicionei a chave ao aplicativo'), verifyForm(pending, onVerified),
  ) : null;
  target.replaceChildren(panel('Verificação em duas etapas',
    muted('Configure seu aplicativo autenticador para acessar sua conta.'), resume,
    pending.length ? muted('Gerar uma nova configuração substitui as chaves ainda pendentes.') : null,
    enroll, status, support(),
  ));
  focusHeading(target);
}
