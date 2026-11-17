import {element, link, muted, notice, panel} from '../ui.js';

const modules = {
  nfse: async () => (await import('./fiscal.js')).renderFiscal,
  'nfse-consulta': async () => (await import('./fiscal.js')).renderFiscal,
  clinica: async () => (await import('./agenda.js')).renderAgenda,
  barbearia: async () => (await import('./agenda.js')).renderAgenda,
  whatsapp: async () => (await import('./whatsapp.js')).renderWhatsApp,
  'cotacao-seguros': async () => (await import('./integrations.js')).renderIntegration,
};

export async function openTool(target, tool, context) {
  const content = element('div', {class: 'stack'});
  target.replaceChildren(link('/portal/produtos', 'Voltar às ferramentas'), content);

  if (Object.hasOwn(modules, tool.slug)) {
    content.replaceChildren(notice('Carregando ferramenta…'));
    const render = await modules[tool.slug]();
    if (!context.signal.aborted) await render(content, {...context, tool});
    return;
  }

  let external;
  if (tool.launch_url) {
    const url = new URL(tool.launch_url);
    if (url.protocol === 'https:' && !url.username && !url.password) {
      external = element('a', {
        href: url.href, class: 'button', target: '_blank', rel: 'noopener noreferrer',
      }, 'Abrir ferramenta');
    }
  }
  content.replaceChildren(panel(tool.name, tool.description ? muted(tool.description) : null,
    external ?? notice('A equipe da FuturaNext ainda precisa configurar o acesso a esta ferramenta.')));
}
