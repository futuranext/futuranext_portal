import {api, resource} from './client.js';
import {
  actionForm, button, element, field, inputField, muted, notice, panel, runAction, statusMessage, UserMessage,
} from './ui.js';

const currency = new Intl.NumberFormat('pt-BR', {style: 'currency', currency: 'BRL'});
const month = new Intl.DateTimeFormat('pt-BR', {year: 'numeric', month: 'long', timeZone: 'UTC'});
const day = new Intl.DateTimeFormat('pt-BR', {dateStyle: 'short', timeZone: 'UTC'});
const statuses = new Map([
  ['open', 'Em aberto'], ['paid', 'Paga'], ['confirmed', 'Pagamento confirmado'],
  ['overdue', 'Vencida'], ['processing', 'Em processamento'],
  ['refunded', 'Estornada'], ['cancelled', 'Cancelada'], ['review', 'Em revisão'],
]);
const methods = [['boleto', 'Boleto'], ['pix', 'Pix'], ['card', 'Cartão de crédito']];

export const money = (cents) => currency.format(cents / 100);

const shortDate = (value) => value ? value.split('-').reverse().join('/') : '';

// Situação da assinatura mensal em uma frase (portal e administração).
export function subscriptionText(subscription, admin = false) {
  if (!subscription) {
    return admin ? 'Cobrança automática desligada. Ainda não há assinatura para esta empresa.'
      : 'A empresa ainda não tem assinatura mensal.';
  }
  const value = subscription.value_cents ? money(subscription.value_cents) : null;
  const next = subscription.next_due_date ? ` Próximo vencimento: ${shortDate(subscription.next_due_date)}.` : '';
  if (!subscription.enabled) return admin ? 'Cobrança automática desligada.' : 'A cobrança mensal está pausada.';
  if (subscription.status === 'active') return `Assinatura ativa: ${value} por mês.${next}`;
  if (subscription.status === 'error') {
    return admin ? 'Cobrança ligada, mas o Asaas recusou ou não confirmou a assinatura. Desligue e ligue de novo ou confira no Asaas.'
      : 'A assinatura está em conferência pela FuturaNext.';
  }
  return admin ? 'Cobrança ligada, aguardando o vínculo com o Asaas.' : 'A assinatura está sendo preparada.';
}

export function invoiceItems(items) {
  return element('ul', {class: 'record-list'}, items.map(({name, amount_cents: cents}) => (
    element('li', {class: 'summary-row'}, element('span', {}, name), element('strong', {}, money(cents)))
  )));
}

export function invoiceRecords(invoices, actionFor) {
  if (!invoices.length) return muted('Nenhuma fatura registrada para esta empresa.');
  return element('ul', {class: 'record-list'}, invoices.map((invoice) => {
    const period = month.format(new Date(`${invoice.period}-01T00:00:00Z`));
    const due = day.format(new Date(`${invoice.due_date}T00:00:00Z`));
    return element('li', {class: 'record'},
      element('div', {class: 'stack compact'},
        element('h3', {}, `Competência ${period}`),
        element('p', {}, `${money(invoice.total_cents)} · ${statuses.get(invoice.status) ?? 'Situação indisponível'}`),
        muted(`Vencimento em ${due}`),
        invoice.reference ? muted(`Referência Asaas ${invoice.reference}`) : null,
        invoice.items.length ? element('details', {}, element('summary', {}, 'Ver serviços cobrados'), invoiceItems(invoice.items)) : null,
      ),
      actionFor(invoice),
    );
  }));
}

async function copy(text, input) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    input?.select();
    if (!document.execCommand?.('copy')) throw new UserMessage('Não foi possível copiar. Selecione o código e copie manualmente.');
  }
}

function copyField(label, value) {
  const input = element('input', {type: 'text', readonly: true, value, spellcheck: 'false'});
  input.addEventListener('focus', () => input.select());
  return {input, control: field(label, input)};
}

function copyButton(label, value, input, status, done) {
  const control = button(label);
  control.addEventListener('click', runAction(control, status, async () => {
    await copy(value, input);
    return done;
  }));
  return control;
}

function cardForm(pay) {
  const year = new Date().getFullYear();
  const months = Array.from({length: 12}, (_, index) => String(index + 1).padStart(2, '0'));
  const years = Array.from({length: 16}, (_, index) => String(year + index));
  const options = (values) => values.map((value) => element('option', {value}, value));
  return actionForm([
    muted('Os dados do cartão são enviados com segurança ao Asaas no momento do pagamento e não ficam guardados na FuturaNext.'),
    element('div', {class: 'form-grid'},
      inputField('Nome impresso no cartão', 'holder_name', {required: true, minlength: 2, maxlength: 100, autocomplete: 'cc-name', autocapitalize: 'characters'}),
      inputField('Número do cartão', 'number', {required: true, inputmode: 'numeric', autocomplete: 'cc-number', maxlength: 23, pattern: '[0-9 ]{13,23}', placeholder: '0000 0000 0000 0000'}),
    ),
    element('div', {class: 'form-grid'},
      field('Mês de validade', element('select', {name: 'expiry_month', required: true, autocomplete: 'cc-exp-month'}, options(months))),
      field('Ano de validade', element('select', {name: 'expiry_year', required: true, autocomplete: 'cc-exp-year'}, options(years))),
      inputField('Código de segurança (CVV)', 'ccv', {required: true, inputmode: 'numeric', autocomplete: 'cc-csc', pattern: '[0-9]{3,4}', maxlength: 4}),
    ),
    element('h4', {}, 'Dados do titular do cartão'),
    element('div', {class: 'form-grid'},
      inputField('CPF ou CNPJ do titular', 'cpf_cnpj', {required: true, inputmode: 'numeric', maxlength: 24}),
      inputField('E-mail', 'email', {type: 'email', required: true, autocomplete: 'email', maxlength: 254}),
      inputField('Telefone com DDD', 'phone', {type: 'tel', required: true, autocomplete: 'tel', maxlength: 24}),
      inputField('CEP', 'postal_code', {required: true, inputmode: 'numeric', autocomplete: 'postal-code', pattern: '[0-9]{5}-?[0-9]{3}', maxlength: 9}),
      inputField('Número do endereço', 'address_number', {required: true, maxlength: 20}),
    ),
  ], 'Pagar com cartão', async (data) => {
    const body = Object.fromEntries(['holder_name', 'number', 'ccv', 'cpf_cnpj', 'email', 'phone', 'postal_code', 'address_number']
      .map((name) => [name, String(data.get(name) ?? '').trim()]));
    body.number = body.number.replace(/\s+/g, '');
    if (!/^[0-9]{13,19}$/.test(body.number)) throw new UserMessage('Confira o número do cartão.');
    body.expiry_month = Number(data.get('expiry_month'));
    body.expiry_year = Number(data.get('expiry_year'));
    return pay(body);
  }, {singleUse: true});
}

export async function renderBillingPortal(target, {tenant, signal, canPay = true}) {
  const base = `/tenants/${resource(tenant.id)}/billing`;
  let model = await api(base, {signal});
  if (signal?.aborted) return;

  const list = element('div', {class: 'stack'});
  const refresh = button('Atualizar faturas', true);
  const status = statusMessage();
  let revision = 0;

  async function reload(message) {
    const current = ++revision;
    const next = await api(base, {signal});
    if (signal?.aborted || current !== revision) return;
    model = next;
    draw();
    if (message) {
      status.className = 'status-message success';
      status.textContent = message;
    }
  }

  function boletoView(area, path, data) {
    const feedback = statusMessage();
    const {input, control} = copyField('Linha digitável (código de barras)', data.identification_field);
    const download = button('Baixar PDF', true);
    download.addEventListener('click', runAction(download, feedback, async () => {
      const blob = await api(`${path}/boleto.pdf`, {signal, responseType: 'blob'});
      const url = URL.createObjectURL(blob);
      const anchor = element('a', {href: url, download: 'boleto-futuranext.pdf'});
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      return 'Download iniciado.';
    }));
    const open = data.pdf_url ? element('a', {
      class: 'button secondary', href: data.pdf_url, target: '_blank', rel: 'noopener noreferrer',
    }, 'Abrir boleto') : null;
    area.replaceChildren(
      control,
      element('div', {class: 'form-actions'},
        copyButton('Copiar código de barras', data.identification_field, input, feedback, 'Código copiado. Cole no aplicativo do seu banco.'),
        open, download),
      feedback,
      muted('A confirmação do boleto pode levar até 3 dias úteis após o pagamento.'),
    );
  }

  function pixView(area, data) {
    const feedback = statusMessage();
    const {input, control} = copyField('Pix copia e cola', data.payload);
    area.replaceChildren(
      element('img', {class: 'payment-qr', src: data.image, alt: 'QR Code Pix para pagamento', width: 240, height: 240}),
      muted('Abra o aplicativo do seu banco, escolha pagar com Pix e leia o QR Code, ou copie o código abaixo.'),
      control,
      element('div', {class: 'form-actions'},
        copyButton('Copiar código Pix', data.payload, input, feedback, 'Código Pix copiado.')),
      feedback,
      muted('A confirmação do Pix costuma ser imediata. Depois de pagar, clique em Atualizar faturas.'),
    );
  }

  function cardView(area, path) {
    area.replaceChildren(cardForm(async (body) => {
      const result = await api(`${path}/card`, {method: 'POST', body, signal});
      if (signal?.aborted) return null;
      if (result.challenge_url) {
        area.replaceChildren(notice(result.message), element('a', {
          class: 'button', href: result.challenge_url, target: '_blank', rel: 'noopener noreferrer',
        }, 'Confirmar no banco'));
        return null;
      }
      await reload(result.message);
      return null;
    }));
    area.querySelector('input')?.focus();
  }

  function paymentOptions(invoice) {
    const path = `${base}/invoices/${resource(invoice.id)}/payment`;
    const area = element('div', {class: 'stack compact'});
    const feedback = statusMessage();
    const choices = methods.map(([key, label]) => {
      const control = button(label, true);
      control.setAttribute('aria-pressed', 'false');
      control.setAttribute('aria-label', `Pagar com ${label}, competência ${invoice.period}`);
      control.addEventListener('click', runAction(control, feedback, async () => {
        for (const other of choices) other.setAttribute('aria-pressed', String(other === control));
        area.replaceChildren(muted('Carregando…'));
        try {
          if (key === 'boleto') boletoView(area, path, await api(`${path}/boleto`, {method: 'POST', signal}));
          else if (key === 'pix') pixView(area, await api(`${path}/pix`, {method: 'POST', signal}));
          else cardView(area, path);
        } catch (error) {
          area.replaceChildren();
          throw error;
        }
        return null;
      }));
      return control;
    });
    return element('div', {class: 'stack compact payment-options'},
      element('strong', {}, 'Escolha a forma de pagamento'),
      element('div', {class: 'form-actions', role: 'group', 'aria-label': 'Forma de pagamento'}, choices),
      feedback, area);
  }

  function action(invoice) {
    if (!canPay) return null;
    if (invoice.status === 'processing') {
      const check = button('Consultar pagamento', true);
      const feedback = statusMessage();
      check.addEventListener('click', runAction(check, feedback, async () => {
        await api(`${base}/invoices/${resource(invoice.id)}/payment`, {method: 'POST', signal});
        await reload('Situação do pagamento atualizada.');
        return null;
      }));
      return element('div', {class: 'stack compact'}, check, feedback);
    }
    if (!['open', 'overdue'].includes(invoice.status)) return null;
    if (!model.payment_available) return muted('Pagamento online indisponível.');
    if (!model.customer_ready) return muted('Pagamento em preparação.');
    return paymentOptions(invoice);
  }

  function draw() {
    list.replaceChildren(invoiceRecords(model.invoices, action));
  }

  refresh.addEventListener('click', runAction(refresh, status, async () => {
    await reload();
    return 'Faturas atualizadas.';
  }));

  target.replaceChildren(panel('Pagamento',
    canPay ? muted('Pague a mensalidade por boleto, Pix ou cartão de crédito, aqui mesmo no portal.') : null,
    model.subscription ? element('p', {class: 'summary-row'}, element('strong', {}, subscriptionText(model.subscription))) : null,
    canPay && model.payment_available && !model.customer_ready
      ? muted('O cadastro de pagamento da empresa está em preparação. Caso persista, fale com a FuturaNext.')
      : null,
    element('div', {class: 'form-actions'}, refresh), status, list,
  ));
  draw();
}
