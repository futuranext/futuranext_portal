import {api, resource} from '../client.js';
import {element, muted, notice, panel} from '../ui.js';

function externalPortal(href, label) {
  if (!href) return null;
  return element('a', {
    class: 'button secondary', href, target: '_blank', rel: 'noopener noreferrer',
  }, label);
}

export async function renderIntegration(target, {tenant, tool, signal}) {
  const path = `/tenants/${resource(tenant.id)}/tools/${resource(tool.slug)}`;
  const settings = await api(path, {signal});
  if (signal.aborted) return;
  const launch = externalPortal(settings.portal_url, 'Abrir portal de cotações');
  target.replaceChildren(panel('Cotação de Seguros Equinos', launch
    ? [muted('Entre com sua conta do portal de cotações.'), launch]
    : notice('O endereço do portal de cotações desta empresa aguarda configuração.')));
}
