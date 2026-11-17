import {api, resource} from '../client.js';
import {
  actionForm, button, checkField, dateTime, definitionList, element, field, inputField, muted,
  notice, panel, runAction, statusMessage, UserMessage,
} from '../ui.js';

const states = {
  connected: 'Conectado', connecting: 'Aguardando leitura do QR Code', disconnected: 'Desconectado',
  creating: 'Preparando conexão', expired: 'Sessão expirada. Gere um novo QR Code.',
  error: 'Falha ao preparar a conexão. Tente conectar de novo.',
};
const QR_POLL_MS = 3000;
const QR_TIMEOUT_MS = 120000;

function readonlyField(label, value, hint) {
  const input = element('input', {type: 'text', readonly: true, value, spellcheck: 'false', autocapitalize: 'none'});
  input.addEventListener('focus', () => input.select());
  return field(label, input, hint);
}

function oneTimeSecret(label, value, hint) {
  return element('div', {class: 'stack compact'},
    notice('Copie agora. Por segurança, este valor não será exibido novamente.'),
    readonlyField(label, value, hint));
}

function consentToken() {
  const match = /^#aceite=([A-Za-z0-9_-]{20,200})$/.exec(location.hash);
  if (match) history.replaceState(null, '', location.pathname + location.search);
  return match?.[1];
}

function sample(status) {
  const base = status.api_base_url ?? 'https://api.futuranext.com.br/whatsapp';
  return [
    'import uuid, httpx',
    '',
    `API = "${base}"`,
    'CHAVE = "wp_..."  # chave criada acima, guardada em variável de ambiente',
    `INSTANCIA = "${status.instance?.id ?? 'ID_DA_INSTANCIA'}"`,
    '',
    'resposta = httpx.post(f"{API}/api/v1/messages/send",',
    '    headers={"Authorization": f"Bearer {CHAVE}"},',
    '    json={"instance_id": INSTANCIA, "phone": "5511999999999",',
    '          "message": "Olá! Sua mensagem chegou.", "idempotency_key": str(uuid.uuid4())})',
    'print(resposta.status_code, resposta.json())',
  ].join('\n');
}

export async function renderWhatsApp(target, {tenant, signal}) {
  const path = `/tenants/${resource(tenant.id)}/tools/whatsapp`;
  let status;
  let message;
  let qrTimer;
  const token = consentToken();

  signal.addEventListener('abort', () => clearTimeout(qrTimer));

  if (token) {
    try {
      status = await api(`${path}/consent/confirm`, {method: 'POST', body: {token}, signal});
      message = notice('Aceite confirmado. Agora você pode conectar o WhatsApp da empresa.');
    } catch (error) {
      message = notice(error instanceof UserMessage ? error.message : 'Não foi possível confirmar o aceite.', true);
    }
  }
  status ??= await api(path, {signal});
  if (signal.aborted) return;

  async function mutate(method, suffix, body) {
    const updated = await api(`${path}${suffix}`, {method, body, signal});
    if (signal.aborted) return null;
    status = updated;
    return updated;
  }

  function stopQr() {
    clearTimeout(qrTimer);
    qrTimer = undefined;
  }

  // --- Aceite ---------------------------------------------------------------------------------

  function consentPanel() {
    const {consent} = status;
    if (consent.status === 'accepted') {
      return panel('1. Aceite de uso',
        muted(`Aceito por ${consent.email} em ${dateTime(consent.accepted_at)}.`));
    }
    const terms = element('ul', {}, status.terms.map((item) => element('li', {}, item)));
    const send = button(consent.status === 'pending' ? 'Reenviar e-mail de aceite' : 'Enviar e-mail de aceite');
    const feedback = statusMessage();
    send.addEventListener('click', runAction(send, feedback, async () => {
      await mutate('POST', '/consent');
      draw();
      return null;
    }));
    const refresh = button('Já aceitei, atualizar', true);
    refresh.addEventListener('click', runAction(refresh, feedback, async () => {
      status = await api(path, {signal});
      if (!signal.aborted) draw();
      return null;
    }));
    const pending = consent.status === 'pending'
      ? notice(`Enviamos um link para ${consent.email}. Abra o e-mail neste navegador, com sua conta conectada, e clique em "Li e aceito". O link vale até ${dateTime(consent.expires_at)}.`)
      : null;
    return panel('1. Aceite de uso',
      muted('Antes de conectar, confirme pelo e-mail da sua conta que você está de acordo com:'),
      terms, pending,
      consent.status === 'expired' ? notice('O link anterior expirou. Envie um novo e-mail.', true) : null,
      element('div', {class: 'form-actions'}, send, consent.status === 'pending' ? refresh : null), feedback);
  }

  // --- Conexão --------------------------------------------------------------------------------

  function connectionPanel() {
    const instance = status.instance;
    const state = instance?.connection_state;
    const details = [['Situação', instance ? states[state] ?? state : 'Ainda não conectado']];
    if (instance?.phone_masked) details.push(['Número', instance.phone_masked]);
    if (instance?.last_connected_at) details.push(['Última conexão', dateTime(instance.last_connected_at)]);
    if (instance) details.push(['ID da instância', instance.id]);

    const items = [definitionList(details)];
    if (!status.can_manage) {
      items.push(muted('Somente administradores da empresa podem conectar ou desconectar o número.'));
      return panel('Conexão do WhatsApp', ...items);
    }
    if (status.consent.status !== 'accepted') {
      items.push(muted('Disponível depois do aceite.'));
      return panel('2. Conexão do WhatsApp', ...items);
    }

    const feedback = statusMessage();
    const qrArea = element('div', {class: 'stack compact'});
    if (state === 'connected') {
      const disconnect = button('Desconectar número', true);
      disconnect.addEventListener('click', runAction(disconnect, feedback, async () => {
        if (!window.confirm('Desconectar o WhatsApp? O bot deixará de enviar e receber mensagens até um novo pareamento.')) return null;
        await mutate('POST', '/disconnect');
        draw();
        return null;
      }));
      items.push(element('div', {class: 'form-actions'}, disconnect));
    } else {
      const connect = button(instance ? 'Gerar QR Code' : 'Conectar WhatsApp');
      connect.addEventListener('click', runAction(connect, feedback, async () => {
        stopQr();
        await mutate('POST', '/connect');
        if (status.instance?.connection_state === 'connected') {
          draw('WhatsApp conectado.');
          return null;
        }
        void pollQr(qrArea, Date.now());
        return null;
      }));
      items.push(
        muted('No celular, abra o WhatsApp › Configurações › Aparelhos conectados › Conectar um aparelho e leia o QR Code.'),
        element('div', {class: 'form-actions'}, connect), qrArea);
    }
    items.push(feedback);
    return panel('2. Conexão do WhatsApp', ...items);
  }

  async function pollQr(area, startedAt) {
    if (signal.aborted) return;
    if (Date.now() - startedAt > QR_TIMEOUT_MS) {
      area.replaceChildren(notice('O QR Code expirou. Clique em "Gerar QR Code" para tentar de novo.', true));
      return;
    }
    try {
      const current = await api(path, {signal});
      if (signal.aborted) return;
      if (current.instance?.connection_state === 'connected') {
        status = current;
        draw('WhatsApp conectado com sucesso.');
        return;
      }
      const qr = await api(`${path}/qr`, {signal});
      if (signal.aborted) return;
      area.replaceChildren(
        element('img', {src: qr.base64, alt: 'QR Code para conectar o WhatsApp', width: 264, height: 264}),
        muted('O código é atualizado automaticamente. Mantenha esta página aberta até conectar.'),
      );
    } catch {
      // QR ainda não gerado (409) ou falha momentânea: tenta de novo até o limite.
      if (signal.aborted) return;
      area.replaceChildren(muted('Preparando o QR Code…'));
    }
    qrTimer = setTimeout(() => void pollQr(area, startedAt), QR_POLL_MS);
  }

  // --- Webhook --------------------------------------------------------------------------------

  function webhookPanel(secret) {
    const current = status.webhook;
    const form = actionForm([
      inputField('URL do webhook do seu bot', 'url', {
        type: 'url', required: true, maxlength: 2048, value: current?.url ?? '', placeholder: 'https://seu-bot.com.br/webhooks/whatsapp',
        spellcheck: 'false', autocapitalize: 'none',
      }, 'Endereço HTTPS público que recebe as mensagens e os status. Sem ? nem #.'),
      checkField('Enviar telefone e texto das mensagens recebidas', 'include_message', current?.include_message ?? true),
      current ? checkField('Gerar um novo segredo de assinatura', 'rotate_secret', false) : null,
    ], current ? 'Salvar webhook' : 'Cadastrar webhook', async (data) => {
      const updated = await mutate('PUT', '/webhook', {
        url: data.get('url').trim(), include_message: data.has('include_message'), rotate_secret: data.has('rotate_secret'),
      });
      if (updated) draw('Webhook salvo.', {webhook: updated.webhook_secret});
      return null;
    });
    const remove = current ? button('Remover webhook', true) : null;
    const feedback = statusMessage();
    remove?.addEventListener('click', runAction(remove, feedback, async () => {
      if (!window.confirm('Remover o webhook? O bot deixará de receber eventos.')) return null;
      await mutate('DELETE', '/webhook');
      draw('Webhook removido.');
      return null;
    }));
    return panel('3. Webhook do seu bot',
      muted('Cada mensagem recebida, status de entrega e mudança de conexão é enviada por POST ao seu bot, assinada com HMAC-SHA256 nos cabeçalhos X-Timestamp e X-Signature (sha256=…), calculada sobre "timestamp.corpo".'),
      secret ? oneTimeSecret('Segredo do webhook', secret, 'Use para validar a assinatura no seu bot.') : null,
      current ? definitionList([['Atual', current.url], ['Atualizado', current.updated_at ? dateTime(current.updated_at) : '—']]) : null,
      form, remove ? element('div', {class: 'form-actions'}, remove) : null, feedback);
  }

  // --- Chaves ---------------------------------------------------------------------------------

  function keysPanel(secret) {
    const list = status.keys.length ? element('ul', {class: 'record-list'}, status.keys.map((key) => {
      const revoke = button('Revogar', true);
      revoke.setAttribute('aria-label', `Revogar a chave ${key.name}`);
      const feedback = statusMessage();
      revoke.addEventListener('click', runAction(revoke, feedback, async () => {
        if (!window.confirm(`Revogar a chave "${key.name}"? O bot que a usa deixará de enviar mensagens.`)) return null;
        await mutate('DELETE', `/keys/${resource(key.id)}`);
        draw('Chave revogada.');
        return null;
      }));
      return element('li', {class: 'record'},
        element('div', {class: 'stack compact'}, element('strong', {}, key.name),
          muted(`${key.prefix}… · criada em ${dateTime(key.created_at)} · expira em ${dateTime(key.expires_at)}`)),
        element('div', {class: 'stack compact'}, revoke, feedback));
    })) : muted('Nenhuma chave ativa.');
    const form = actionForm([
      inputField('Nome da chave', 'name', {required: true, maxlength: 80, placeholder: 'Bot de atendimento'}),
    ], 'Criar chave de API', async (data, keyForm) => {
      const updated = await mutate('POST', '/keys', {name: data.get('name').trim()});
      if (updated) {
        keyForm.reset();
        draw('Chave criada.', {key: updated.api_key});
      }
      return null;
    });
    return panel('4. Chaves de API para enviar mensagens',
      secret ? oneTimeSecret('Nova chave de API', secret, 'Envie no cabeçalho Authorization: Bearer <chave>.') : null,
      status.api_base_url ? readonlyField('Endereço da API', status.api_base_url) : null,
      list, form,
      element('details', {class: 'edit-disclosure'}, element('summary', {}, 'Exemplo em Python'),
        element('pre', {}, element('code', {}, sample(status)))));
  }

  // --- Montagem -------------------------------------------------------------------------------

  function draw(text, secrets = {}) {
    stopQr();
    if (signal.aborted) return;
    if (!status.configured) {
      target.replaceChildren(panel('WhatsApp', notice('A conexão de WhatsApp está sendo preparada pela FuturaNext. Tente novamente mais tarde.')));
      return;
    }
    const sections = [text ? notice(text) : message, consentPanel(), connectionPanel()];
    if (!status.can_manage) sections.splice(1, 1);
    if (status.can_manage && status.consent.status === 'accepted' && status.instance) {
      sections.push(webhookPanel(secrets.webhook), keysPanel(secrets.key));
    }
    message = null;
    target.replaceChildren(...sections.filter(Boolean));
  }

  draw();
}
