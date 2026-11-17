import {api, resource} from './client.js';
import {button, element, muted, notice, panel, runAction, statusMessage, UserMessage} from './ui.js';

const currency = new Intl.NumberFormat('pt-BR', {style: 'currency', currency: 'BRL'});
const month = new Intl.DateTimeFormat('pt-BR', {year: 'numeric', month: 'long', timeZone: 'UTC'});
const day = new Intl.DateTimeFormat('pt-BR', {dateStyle: 'short', timeZone: 'UTC'});
const statuses = new Map([
  ['open', 'Em aberto'], ['paid', 'Paga'], ['confirmed', 'Pagamento confirmado'],
  ['overdue', 'Vencida'], ['processing', 'Em processamento'],
  ['refunded', 'Estornada'], ['cancelled', 'Cancelada'], ['review', 'Em revisão'],
]);
const paymentOrigins = new Set(['https://www.asaas.com', 'https://asaas.com', 'https://sandbox.asaas.com']);

export const money = (cents) => currency.format(cents / 100);

export function invoiceItems(items) {
  return element('ul', {class: 'record-list'}, items.map(({name, amount_cents: cents}) => (
    element('li', {class: 'summary-row'}, element('span', {}, name), element('strong', {}, money(cents)))
  )));
}

export function invoiceRecords(invoices, actionFor) {
  if (!invoices.length) return muted('Nenhuma cobrança registrada para esta empresa.');
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

function checkoutUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return paymentOrigins.has(url.origin) && !url.username && !url.password ? url.href : null;
  } catch {
    return null;
  }
}

function paymentAction(invoice, checkoutAvailable, startPayment, focusInvoice) {
  const processing = invoice.status === 'processing' && !invoice.payment_url;
  if (!processing && !['open', 'overdue'].includes(invoice.status)) return null;
  const url = checkoutUrl(invoice.payment_url);
  if (url && checkoutAvailable) {
    const pay = element('a', {
      class: 'button', href: url, target: '_blank', rel: 'noopener noreferrer',
      'aria-label': `Pagar no Asaas. Competência ${invoice.period}. Abre em outra aba.`,
    }, 'Pagar no Asaas');
    if (focusInvoice === invoice.id) pay.dataset.focus = 'payment';
    return pay;
  }
  if (!checkoutAvailable) return muted('Pagamento online indisponível.');
  if (invoice.payment_url) return notice('O link de pagamento está indisponível.', true);
  if (invoice.status === 'overdue') return muted('Solicite um novo vencimento ao administrador.');

  const pay = button(processing ? 'Consultar pagamento' : 'Preparar pagamento no Asaas');
  pay.setAttribute('aria-label', `${pay.textContent} para a competência ${invoice.period}`);
  const status = statusMessage();
  pay.addEventListener('click', runAction(pay, status, () => startPayment(invoice.id), {singleUse: true}));
  return element('div', {class: 'stack compact'}, pay, status);
}

export async function renderBillingPortal(target, {tenant, signal, canPay = true}) {
  const base = `/tenants/${resource(tenant.id)}/billing`;
  let model = await api(base, {signal});
  if (signal?.aborted) return;

  const list = element('div', {class: 'stack'});
  const refresh = button('Atualizar cobranças', true);
  const status = statusMessage();
  let revision = 0;

  function draw(focusInvoice) {
    list.replaceChildren(invoiceRecords(model.invoices, (invoice) => (
      canPay ? paymentAction(invoice, model.payment_available, startPayment, focusInvoice) : null
    )));
    if (focusInvoice) list.querySelector('[data-focus="payment"]')?.focus();
  }

  async function startPayment(invoiceId) {
    const updated = await api(`${base}/invoices/${resource(invoiceId)}/payment`, {method: 'POST', signal});
    if (signal?.aborted) return;
    revision++;
    model.invoices = model.invoices.map((invoice) => invoice.id === updated.id ? updated : invoice);
    const paymentReady = ['open', 'overdue'].includes(updated.status) && Boolean(checkoutUrl(updated.payment_url));
    status.className = 'status-message';
    status.textContent = paymentReady
      ? 'Pagamento disponível. Abra a fatura para escolher a forma de pagamento.'
      : 'Situação da cobrança atualizada. Consulte novamente se o pagamento ainda estiver em processamento.';
    draw(paymentReady ? updated.id : null);
  }

  refresh.addEventListener('click', runAction(refresh, status, async () => {
    const current = ++revision;
    const next = await api(base, {signal});
    if (signal?.aborted || current !== revision) return;
    model = next;
    draw();
    return 'Cobranças atualizadas.';
  }));

  target.replaceChildren(panel('Cobranças da empresa',
    canPay ? muted('Escolha boleto, Pix ou cartão de crédito na página de pagamento do Asaas.') : null,
    canPay && model.payment_available && !model.customer_ready
      ? muted('O cadastro de pagamento da empresa está em preparação. Caso persista, fale com a FuturaNext.')
      : null,
    element('div', {class: 'form-actions'}, refresh), status, list,
  ));
  draw();
}
