import {api, resource} from './client.js';
import {invoiceItems, invoiceRecords, money} from './billing-portal.js';
import {
  actionForm, button, checkField, element, field, inputField, muted, notice, panel,
  runAction, select, statusMessage, UserMessage,
} from './ui.js';

const clientsPath = '/admin/clients';
const billingDate = new Intl.DateTimeFormat('en-CA', {year: 'numeric', month: '2-digit', day: '2-digit', timeZone: 'America/Sao_Paulo'});
const environments = {sandbox: 'Sandbox', production: 'Produção'};
const serviceLabel = ({name, amount_cents: cents, active}) => `${name} · ${money(cents)} · ${active ? 'Ativo' : 'Inativo'}`;

function localToday() {
  const parts = Object.fromEntries(billingDate.formatToParts(new Date()).map(({type, value}) => [type, value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function serviceForm(service, mutate) {
  return actionForm([
    element('div', {class: 'form-grid'},
      inputField('Nome do serviço', 'name', {value: service?.name, minlength: 2, maxlength: 120, required: true}),
      inputField('Valor mensal em reais', 'amount', {
        type: 'number', inputmode: 'decimal', min: '.01', max: 100000, step: '.01', value: service ? service.amount_cents / 100 : '', required: true,
      }),
    ),
    checkField('Serviço ativo', 'active', service?.active ?? true),
  ], service ? 'Salvar serviço' : 'Cadastrar serviço', async (formData, form) => {
    const amount = Math.round(Number(formData.get('amount')) * 100);
    const name = formData.get('name').trim();
    if (name.length < 2) throw new UserMessage('Informe um nome para o serviço com pelo menos dois caracteres.');
    if (!Number.isSafeInteger(amount) || amount <= 0) throw new UserMessage('Informe um valor mensal maior que zero.');
    if (amount > 10000000) throw new UserMessage(`O valor máximo mensal por serviço é ${money(10000000)}.`);
    await mutate({name, amount_cents: amount, active: formData.has('active')});
    if (!service) form.reset();
    return service ? 'Serviço atualizado.' : 'Serviço cadastrado.';
  });
}

function accountPanel(model, save) {
  const configured = element('p', {class: 'muted'});
  function updateStatus() {
    if (!model.account) configured.textContent = 'Vincule o cliente para disponibilizar o pagamento online.';
    else if (model.account.environment !== model.environment) {
      configured.textContent = `O cliente atual está vinculado ao ambiente ${environments[model.account.environment]}.`;
    } else configured.textContent = 'Cliente Asaas vinculado.';
  }
  updateStatus();
  const customer = inputField('ID do cliente no Asaas', 'customer_id', {
    value: model.account?.customer_id, placeholder: 'cus_…', maxlength: 124, pattern: 'cus_[a-zA-Z0-9_\\-]{1,120}',
    required: true, spellcheck: 'false', autocapitalize: 'none',
  }, 'Use o identificador do cliente neste ambiente do Asaas.');
  const accountForm = actionForm([customer], 'Salvar vínculo', async (data) => {
    await save(data.get('customer_id').trim());
    updateStatus();
    return 'Vínculo atualizado.';
  });
  return panel('Cliente Asaas', muted(`Ambiente atual · ${environments[model.environment]}`), configured,
    model.payment_available && model.webhook_configured === false
      ? muted('Configure o webhook do Asaas para receber os estados automaticamente. Por enquanto, use Atualizar situação.')
      : null,
    accountForm);
}

async function companyBilling(target, tenant, signal) {
  const base = `${clientsPath}/${resource(tenant.id)}/billing`;
  const model = await api(base, {signal});
  if (signal.aborted) return;

  const servicesList = element('div', {class: 'stack compact'});
  const summary = element('div', {class: 'stack compact'});
  const invoicesList = element('div', {class: 'stack'});
  const invoiceFeedback = statusMessage();
  const today = localToday();

  const newInvoice = actionForm([
    element('div', {class: 'form-grid'},
      inputField('Competência', 'period', {type: 'month', value: today.slice(0, 7), placeholder: 'AAAA-MM', required: true}),
      inputField('Vencimento', 'due_date', {type: 'date', min: today, required: true}),
    ),
  ], 'Gerar cobrança', async (data) => {
    const period = data.get('period');
    const dueDate = data.get('due_date');
    if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(period)) throw new UserMessage('Informe a competência no formato ano e mês.');
    if (!dueDate || dueDate < localToday()) throw new UserMessage('Escolha um vencimento a partir de hoje.');
    const invoice = await api(`${base}/invoices`, {method: 'POST', body: {period, due_date: dueDate}, signal});
    if (signal.aborted) return;
    storeInvoice(invoice);
    return 'Cobrança registrada.';
  });

  function updateSummary() {
    const included = model.services.filter(({active}) => active);
    let requirement;
    if (model.account?.environment !== model.environment) requirement = 'Vincule o cliente Asaas no ambiente atual para gerar a cobrança.';
    else if (!included.length) requirement = 'Cadastre um serviço ativo para gerar a cobrança.';
    else if (included.length > 100) requirement = 'Mantenha até 100 serviços ativos por cobrança.';
    newInvoice.hidden = Boolean(requirement);

    summary.replaceChildren(...[
      included.length ? invoiceItems(included) : null,
      included.length ? element('p', {class: 'summary-row'}, element('span', {}, 'Total previsto'),
        element('strong', {}, money(included.reduce((amount, service) => amount + service.amount_cents, 0)))) : null,
      requirement ? muted(requirement) : null,
    ].filter(Boolean));
  }

  function serviceRow(initial) {
    const title = element('summary', {}, serviceLabel(initial));
    const form = serviceForm(initial, async (body) => {
      const updated = await api(`${base}/services/${resource(initial.id)}`, {method: 'PATCH', body, signal});
      if (signal.aborted) return;
      model.services = model.services.map((item) => item.id === updated.id ? updated : item);
      title.textContent = serviceLabel(updated);
      updateSummary();
    });
    return element('details', {class: 'edit-disclosure'}, title, form);
  }

  function showInvoices() {
    invoicesList.replaceChildren(invoiceRecords(model.invoices, (invoice) => {
      const status = statusMessage();
      const controls = [];

      function action(label, operation, message, confirm) {
        const control = button(label, true);
        control.setAttribute('aria-label', `${label} da competência ${invoice.period}`);
        control.addEventListener('click', runAction(control, status, async () => {
          if (confirm && !window.confirm(confirm)) return;
          const updated = await api(`${base}/invoices/${resource(invoice.id)}/${operation}`, {method: 'POST', signal});
          if (signal.aborted) return;
          storeInvoice(updated);
          invoiceFeedback.textContent = message;
        }));
        controls.push(control);
      }

      if (model.payment_available && invoice.can_reconcile) {
        action('Atualizar situação', 'reconcile', 'Situação da cobrança atualizada.');
      }
      if (invoice.can_cancel === true) {
        action('Cancelar cobrança', 'cancel', 'Cobrança cancelada.',
          `Cancelar a cobrança da competência ${invoice.period}? Você poderá gerar outra cobrança para este mês.`);
      }
      return controls.length ? element('div', {class: 'stack compact'}, element('div', {class: 'form-actions'}, controls), status) : null;
    }));
  }

  function storeInvoice(updated) {
    const known = model.invoices.some((invoice) => invoice.id === updated.id);
    model.invoices = known
      ? model.invoices.map((invoice) => invoice.id === updated.id ? updated : invoice)
      : [updated, ...model.invoices];
    showInvoices();
  }

  const emptyServices = muted('Nenhum serviço mensal cadastrado.');
  servicesList.replaceChildren(...(model.services.length ? model.services.map(serviceRow) : [emptyServices]));
  const newService = serviceForm(null, async (body) => {
    const service = await api(`${base}/services`, {method: 'POST', body, signal});
    if (signal.aborted) return;
    model.services.push(service);
    emptyServices.remove();
    servicesList.append(serviceRow(service));
    updateSummary();
  });
  const connection = accountPanel(model, async (customerId) => {
    if (!customerId) throw new UserMessage('Informe o ID do cliente no Asaas.');
    model.account = await api(`${base}/account`, {method: 'PUT', body: {customer_id: customerId}, signal});
    if (!signal.aborted) updateSummary();
  });
  updateSummary();
  showInvoices();
  target.replaceChildren(
    connection,
    panel('Serviços mensais',
      element('details', {class: 'edit-disclosure'}, element('summary', {}, 'Novo serviço'), newService), servicesList),
    panel('Gerar cobrança', muted('Os nomes e valores dos serviços ativos ficam registrados na cobrança.'), summary, newInvoice),
    panel('Histórico de cobranças', invoiceFeedback, invoicesList),
  );
}

export async function renderBillingAdmin(target, {signal} = {}) {
  const companies = await api(clientsPath, {signal});
  if (signal?.aborted) return;
  if (!companies.length) {
    target.replaceChildren(notice('Nenhuma empresa disponível.'));
    return;
  }

  const options = [['', 'Selecione uma empresa'], ...companies.map(({id, name, active}) => [id, active ? name : `${name} (inativa)`])];
  const picker = select(options);
  const content = element('div', {class: 'stack'});
  let selection;
  picker.options[0].disabled = true;

  async function openCompany() {
    selection?.abort();
    const tenant = companies.find(({id}) => id === picker.value);
    if (!tenant) return;
    selection = new AbortController();
    const requestSignal = signal ? AbortSignal.any([selection.signal, signal]) : selection.signal;
    content.replaceChildren(notice('Carregando cobranças…'));
    try {
      await companyBilling(content, tenant, requestSignal);
    } catch (error) {
      if (!requestSignal.aborted) content.replaceChildren(notice(
        error instanceof UserMessage ? error.message : 'Não foi possível carregar as cobranças desta empresa.', true,
      ));
    }
  }

  picker.addEventListener('change', () => void openCompany());
  target.replaceChildren(element('div', {class: 'tenant-picker'}, field('Empresa', picker)), content);
  if (companies.length === 1) {
    picker.value = companies[0].id;
    await openCompany();
  }
}
