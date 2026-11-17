import {api, resource} from '../client.js';
import {requireMfa} from '../mfa.js';
import {
  actionForm, button, checkField, dateTime, definitionList, element, field, inputField, muted,
  notice, panel, runAction, select, statusMessage, UserMessage,
} from '../ui.js';

const CERTIFICATE_LIMIT = 128 * 1024;
const environments = [['restrita', 'Produção restrita (testes)'], ['producao', 'Produção']];
const certificateLabels = {
  ausente: 'Nenhum certificado cadastrado',
  vigente: 'Vigente',
  a_vencer: 'Vence em breve',
  vencido: 'Vencido',
  a_vigorar: 'Validade ainda não iniciada',
};

function environmentField(value) {
  return field('Ambiente previsto', select(environments, {name: 'environment'}, value || 'restrita'));
}

function formatCnpj(cnpj) {
  return cnpj.replace(/^(.{2})(.{3})(.{3})(.{4})(.{2})$/, '$1.$2.$3/$4-$5');
}

function certificateDetails(current) {
  const fields = [['Situação', certificateLabels[current.status]]];
  if (current.not_after) {
    fields.push(['Válido até', dateTime(current.not_after)]);
    if (current.days_remaining > 0) fields.push(['Dias restantes', current.days_remaining]);
  }
  return definitionList(fields);
}

async function fileBase64(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let content = '';
  for (let offset = 0; offset < bytes.length; offset += 0x4000) {
    content += String.fromCharCode(...bytes.subarray(offset, offset + 0x4000));
  }
  return btoa(content);
}

function certificatePanel(base, state, canEdit, signal) {
  const details = element('div', {}, certificateDetails(state.certificate));
  const scope = muted('A validade exibida vem do arquivo A1. O acesso fiscal depende dos requisitos da SEFIN ou do ADN.');
  if (!canEdit) return panel('Certificado A1', details, scope);
  if (!state.mfa_verified) {
    const stepUp = button('Verificar em duas etapas', true);
    const status = statusMessage();
    const stepUpTarget = element('div', {class: 'stack'});
    stepUp.addEventListener('click', runAction(stepUp, status, () => requireMfa(stepUpTarget), {singleUse: true}));
    return panel('Certificado A1', details, scope, stepUp, status, stepUpTarget);
  }

  const remove = button('Remover certificado', true);
  remove.hidden = state.certificate.status === 'ausente';
  const removeStatus = statusMessage();
  const show = (certificate) => {
    details.replaceChildren(certificateDetails(certificate));
    remove.hidden = certificate.status === 'ausente';
  };
  remove.addEventListener('click', runAction(remove, removeStatus, async () => {
    if (!window.confirm('Remover o certificado desta ferramenta?')) return;
    await api(base + '/certificate', {method: 'DELETE', signal});
    show({status: 'ausente'});
    return 'Certificado removido.';
  }));
  const upload = state.encryption_configured
    ? actionForm([
      inputField('Arquivo A1', 'certificate', {type: 'file', accept: '.pfx,.p12', required: true},
        'Use um arquivo .pfx ou .p12 de até 128 KiB. Um novo arquivo substitui o atual.'),
      inputField('Senha do certificado', 'password', {type: 'password', autocomplete: 'off', maxlength: 1024}),
    ], 'Salvar certificado', async (data, form) => {
      const file = data.get('certificate');
      if (!file?.name || !/\.(?:pfx|p12)$/i.test(file.name) || file.size > CERTIFICATE_LIMIT) {
        throw new UserMessage('Selecione um arquivo .pfx ou .p12 de até 128 KiB.');
      }
      try {
        const certificate = await api(base + '/certificate', {
          method: 'PUT',
          body: {content_base64: await fileBase64(file), password: data.get('password')},
          signal,
        });
        show(certificate);
        form.reset();
        return 'Certificado salvo.';
      } catch (error) {
        if (error.status === 422) {
          throw new UserMessage('Confira o arquivo, a senha e as datas de validade do certificado.');
        }
        throw error;
      } finally {
        form.elements.namedItem('password').value = '';
      }
    })
    : notice('O armazenamento de certificados precisa ser configurado pela FuturaNext.');
  return panel('Certificado A1', details, scope, upload, remove, removeStatus);
}

const emissionFields = [
  {name: 'issuer_cnpj', label: 'CNPJ do prestador', maxlength: 24, required: true},
  {name: 'municipal_registration', label: 'Inscrição municipal', maxlength: 30},
  {name: 'municipality_ibge', label: 'Código IBGE do município', inputmode: 'numeric', maxlength: 7, pattern: '[0-9]{7}', required: true},
  {name: 'national_tax_code', label: 'Código de tributação nacional', inputmode: 'numeric', maxlength: 6, pattern: '[0-9]{6}', required: true},
  {name: 'municipal_tax_code', label: 'Código de tributação municipal', inputmode: 'numeric', maxlength: 3, pattern: '[0-9]{3}'},
  {name: 'nbs_code', label: 'Código NBS', inputmode: 'numeric', maxlength: 9, pattern: '[0-9]{9}'},
  {name: 'dps_series', label: 'Série da DPS', inputmode: 'numeric', maxlength: 5, pattern: '[0-9]{1,5}', required: true},
  {name: 'service_description', label: 'Descrição padrão do serviço', maxlength: 500},
];

function environmentLabel(value) {
  return environments.find(([id]) => id === value)?.[1];
}

function emissionPanel(base, settings, canEdit, signal) {
  if (!canEdit) {
    const entries = settings && [
      ['Ambiente previsto', environmentLabel(settings.environment)],
      ...emissionFields.filter(({name}) => settings[name]).map(({name, label}) => [
        label, name === 'issuer_cnpj' ? formatCnpj(settings[name]) : settings[name],
      ]),
    ];
    return panel('Dados do prestador', entries ? definitionList(entries) : muted('Dados do prestador pendentes.'));
  }
  const recorded = {dps_series: '1', ...settings};
  const editor = actionForm([
    environmentField(recorded.environment),
    ...emissionFields.map(({label, name, ...attributes}) => inputField(label, name, {
      type: 'text', value: recorded[name] ?? '', ...attributes,
    })),
  ], 'Salvar dados do prestador', async (data, form) => {
    const updated = await api(base + '/settings', {method: 'PUT', body: Object.fromEntries(data), signal});
    form.elements.namedItem('issuer_cnpj').value = formatCnpj(updated.issuer_cnpj);
    return 'Dados do prestador salvos.';
  });
  return panel('Dados do prestador', editor);
}

function consultationPanel(base, settings, canEdit, signal) {
  const companies = settings?.companies ?? [];
  if (!canEdit) {
    const fields = companies.map(({cnpj, name, active}) => [
      name || 'CNPJ', formatCnpj(cnpj) + (active ? '' : ' · Inativo'),
    ]);
    return panel('CNPJs para consulta',
      settings ? definitionList([['Ambiente previsto', environmentLabel(settings.environment)]]) : null,
      fields.length ? definitionList(fields) : muted('Nenhum CNPJ cadastrado.'));
  }
  const rows = element('div', {class: 'stack compact'});
  const add = button('Adicionar CNPJ', true);
  function appendCompany(company) {
    const remove = button('Remover este CNPJ', true);
    const row = element('fieldset', {class: 'stack compact'},
      element('legend', {}, 'Empresa consultada'),
      inputField('CNPJ', 'cnpj', {type: 'text', value: formatCnpj(company.cnpj), maxlength: 24, required: true}),
      inputField('Nome de referência', 'company_name', {type: 'text', value: company.name, maxlength: 100}),
      checkField('Ativo', 'company_active', company.active),
      remove,
    );
    rows.append(row);
    add.disabled = rows.children.length >= 100;
    remove.addEventListener('click', () => {
      row.remove();
      add.disabled = false;
      add.focus();
    });
    return row;
  }
  for (const company of companies) appendCompany(company);
  add.addEventListener('click', () => {
    appendCompany({cnpj: '', name: '', active: true}).querySelector('input').focus();
  });

  const editor = actionForm([
    environmentField(settings?.environment), rows, add,
  ], 'Salvar CNPJs e ambiente', async (data) => {
    const companySettings = Array.from(rows.children, (row) => ({
      cnpj: row.querySelector('[name="cnpj"]').value,
      name: row.querySelector('[name="company_name"]').value,
      active: row.querySelector('[name="company_active"]').checked,
    }));
    await api(base + '/settings', {
      method: 'PUT', body: {environment: data.get('environment'), companies: companySettings}, signal,
    });
    return 'CNPJs e ambiente salvos.';
  });
  return panel('CNPJs para consulta', editor);
}

function templateDownload(base, {label, variant, filename}, signal) {
  const download = button(label, true);
  const status = statusMessage();
  download.addEventListener('click', runAction(download, status, async () => {
    const blob = await api(base + '/template?variant=' + variant, {signal, responseType: 'blob'});
    const objectUrl = URL.createObjectURL(blob);
    const anchor = element('a', {href: objectUrl, download: filename});
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    return 'Modelo baixado.';
  }));
  return element('div', {}, download, status);
}

function templatePanel(base, signal) {
  return panel('Planilha de emissão',
    muted('Escolha o arquivo para o fluxo que sua empresa utiliza.'),
    templateDownload(base, {
      label: 'Baixar modelo genérico',
      variant: 'generico',
      filename: 'modelo-nfse-emissao-v1.0.xlsx',
    }, signal),
    templateDownload(base, {
      label: 'Baixar modelo compatível com o robô CCP',
      variant: 'ccp',
      filename: 'Base_Emissao_Nota_Fiscal.xlsx',
    }, signal),
  );
}

export async function renderFiscal(target, {tenant, tool, signal, canEdit}) {
  const base = '/tenants/' + resource(tenant.id) + '/tools/' + resource(tool.slug) + '/fiscal';
  const state = await api(base, {signal});
  if (signal?.aborted) return;
  const emission = tool.slug === 'nfse';
  const pending = emission
    ? 'A transmissão ao Emissor Nacional depende de homologação e autorização do prestador.'
    : 'A consulta de notas emitidas depende de homologação do acesso ao ADN para os CNPJs cadastrados.';
  target.replaceChildren(
    notice(pending),
    emission ? emissionPanel(base, state.settings, canEdit, signal) : consultationPanel(base, state.settings, canEdit, signal),
    certificatePanel(base, state, canEdit, signal),
    ...(emission ? [templatePanel(base, signal)] : []),
    element('a', {
      href: 'https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/documentacao-atual',
      target: '_blank', rel: 'noopener noreferrer',
    }, 'Documentação oficial do Sistema Nacional NFS-e'),
  );
}
