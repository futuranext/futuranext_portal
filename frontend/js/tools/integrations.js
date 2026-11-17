import {api, resource} from '../client.js';
import {
  actionForm, checkField, dateTime, definitionList, element, field, muted, notice, panel,
} from '../ui.js';

const eventLabels = {
  'message.received': 'Mensagem recebida',
  'message.status': 'Status de mensagem',
  'instance.connection': 'Conexão da instância',
};

function externalPortal(href, label) {
  if (!href) return null;
  return element('a', {
    class: 'button secondary', href, target: '_blank', rel: 'noopener noreferrer',
  }, label);
}

function integrationEvents(events) {
  if (!events.length) return muted('Nenhum evento recebido para esta instância.');
  return element('ul', {class: 'record-list'}, events.map((event) => (
    element('li', {class: 'record'},
      element('div', {class: 'stack compact'},
        element('strong', {}, eventLabels[event.type] ?? event.type),
        muted(`Ocorrido em ${dateTime(event.occurred_at)} · Recebido em ${dateTime(event.received_at)}`),
      ),
    )
  )));
}

function whatsappPanel(target, path, settings, canEdit, signal, message) {
  const launch = externalPortal(settings.portal_url, 'Abrir painel WhatsApp');
  if (!settings.webhook_path) {
    target.replaceChildren(panel('WhatsApp',
      notice('Aguardando vínculo da instância de WhatsApp desta empresa.'), launch,
    ));
    return;
  }

  const endpoint = new URL(
    settings.webhook_path, __FUTURANEXT_PUBLIC_CONFIG__.apiBase || location.origin,
  ).href;
  const webhook = field('URL do webhook', element('input', {
    type: 'url', readonly: true, value: endpoint, spellcheck: false,
  }), 'Use esta URL na configuração do código do seu bot.');
  const details = [['Recebimento', settings.enabled ? 'Ativo' : 'Desativado'],
    ['Instância', settings.instance_id]];
  if (settings.updated_at) details.push(['Última alteração', dateTime(settings.updated_at)]);
  const form = canEdit ? actionForm([
    checkField('Receber eventos enviados pelo bot', 'enabled', settings.enabled),
  ], 'Salvar webhook', async (data) => {
    const updated = await api(`${path}/webhook`, {
      method: 'PUT', body: {enabled: data.has('enabled')}, signal,
    });
    if (!signal.aborted) whatsappPanel(target, path, updated, canEdit, signal, 'Webhook atualizado.');
  }) : null;

  target.replaceChildren(
    panel('Webhook do WhatsApp', message && notice(message), definitionList(details), webhook, form, launch),
    panel('Eventos recentes', integrationEvents(settings.recent_events)),
  );
}

export async function renderIntegration(target, {tenant, tool, signal, canEdit}) {
  const path = `/tenants/${resource(tenant.id)}/tools/${resource(tool.slug)}`;
  const settings = await api(path, {signal});
  if (signal.aborted) return;
  if (tool.slug === 'whatsapp') {
    whatsappPanel(target, path, settings, canEdit, signal);
    return;
  }
  const launch = externalPortal(settings.portal_url, 'Abrir portal de cotações');
  target.replaceChildren(panel('Cotação de Seguros Equinos', launch
    ? [muted('Entre com sua conta do portal de cotações.'), launch]
    : notice('O endereço do portal de cotações desta empresa aguarda configuração.')));
}
