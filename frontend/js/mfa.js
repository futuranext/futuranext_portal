import {auth} from './client.js';
import {actionForm, button, element, focusHeading, inputField, muted, notice, panel, runAction, select, field, statusMessage, UserMessage} from './ui.js';

function verifyForm(factors) {
  const chosen = factors[0].id;
  const chooseFactor = factors.length > 1
    ? field('Autenticador', select(factors.map((factor, index) => [factor.id, factor.friendly_name || `Autenticador ${index + 1}`]), {name: 'factor'}, chosen))
    : null;
  return actionForm([
    chooseFactor,
    inputField('Código do autenticador', 'code', {
      type: 'text', inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: 6, pattern: '[0-9]{6}', required: true,
    }),
  ], 'Verificar código', async (data) => {
    const {error} = await auth.mfa.challengeAndVerify({factorId: data.get('factor') ?? chosen, code: data.get('code')});
    if (error) throw new UserMessage('O código não foi aceito. Use o código atual do seu aplicativo.');
    location.reload();
  }, {singleUse: true});
}

export async function requireMfa(target) {
  const {data, error} = await auth.mfa.listFactors();
  if (error) {
    target.replaceChildren(notice('Não foi possível consultar seus autenticadores. Atualize a página e tente novamente.', true));
    return;
  }
  if (data.totp.length) {
    target.replaceChildren(panel('Verificação em duas etapas', muted('Informe o código de seis dígitos do seu aplicativo autenticador.'), verifyForm(data.totp)));
    return;
  }

  const enroll = button('Configurar autenticador');
  const status = statusMessage();
  enroll.addEventListener('click', runAction(enroll, status, async () => {
    const {data: enrollment, error: failure} = await auth.mfa.enroll({factorType: 'totp', issuer: 'FuturaNext'});
    if (failure || !enrollment.totp) throw new UserMessage('Não foi possível iniciar a configuração. Tente novamente.');
    const {qr_code: qrCode, secret} = enrollment.totp;
    const image = /^data:image\/svg\+xml[;,]/.test(qrCode)
      ? element('img', {class: 'mfa-qr', src: qrCode, alt: 'Código QR para cadastrar o autenticador'})
      : null;
    target.replaceChildren(panel('Adicionar autenticador',
      muted('Leia o código QR no seu aplicativo autenticador ou insira a chave manualmente. Depois, digite o código gerado.'),
      image,
      element('details', {}, element('summary', {}, 'Mostrar chave para inserção manual'), element('code', {class: 'mfa-secret'}, secret)),
      verifyForm([enrollment]),
    ));
    focusHeading(target);
  }, {singleUse: true}));
  target.replaceChildren(panel('Verificação em duas etapas', muted('Configure seu aplicativo autenticador para acessar esta área.'), enroll, status));
}
