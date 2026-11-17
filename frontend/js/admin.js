import {api, resource} from './client.js';
import {members} from './members.js';
import {requireMfa} from './mfa.js';
import {toolRequests, userAccess} from './user-access.js';
import {actionForm, checkField, dateTime, element, field, inputField, link, muted, notice, panel, screen, select, UserMessage} from './ui.js';
import {boot, isStaff, navigation, view} from './workspace.js';

const pages = [
  ['/admin', 'Visão geral', 'overview'],
  ['/admin/clientes', 'Empresas', 'clients'],
  ['/admin/usuarios', 'Usuários', 'members'],
  ['/admin/produtos', 'Catálogo de produtos', 'products'],
  ['/admin/assinaturas', 'Licenças', 'licenses'],
  ['/admin/acessos', 'Acessos por usuário', 'access'],
  ['/admin/solicitacoes', 'Solicitações', 'requests'],
];

const clientsPath = '/admin/clients';
const productsPath = '/admin/products';
const nameField = (name) => inputField('Nome', 'name', {value: name, required: true, minlength: 2, maxlength: 120});
const slugField = () => inputField('Identificador', 'slug', {
  required: true, minlength: 2, maxlength: 63, pattern: '[a-z0-9]+(?:-[a-z0-9]+)*', autocapitalize: 'none', spellcheck: 'false',
}, 'Use letras minúsculas, números e hífens.');

const description = (text) => field('Descrição', element('textarea', {name: 'description', maxlength: 1000, rows: 3}, text));
const activeLabel = (active) => active ? 'Ativo' : 'Inativo';
const productFields = (item = {}) => [
  description(item.description ?? ''),
  inputField('URL externa da ferramenta', 'launch_url', {type: 'url', maxlength: 2048, value: item.launch_url},
    'Para ferramentas por link. Use HTTPS, sem credenciais ou parâmetros.'),
  checkField('Oferecer para aquisição no portal', 'offerable', item.offerable),
];

function productValues(data) {
  return {
    description: data.get('description').trim(),
    launch_url: data.get('launch_url').trim() || null,
    offerable: data.has('offerable'),
  };
}

async function catalog(canEdit, products) {
  const path = products ? productsPath : clientsPath;
  const label = products ? 'produto' : 'empresa';
  const records = await api(path);
  const list = element('div', {class: 'stack compact'});

  function drawRecords() {
    list.replaceChildren(...records.map((item) => {
      const title = () => `${item.name} · ${item.slug} · ${activeLabel(item.active)}`;
      if (!canEdit) return muted(title());

      const summary = element('summary', {}, title());
      const form = actionForm([
        nameField(item.name),
        ...(products ? productFields(item) : []),
        checkField(products ? 'Produto ativo' : 'Empresa ativa', 'active', item.active),
      ].filter(Boolean), `Salvar ${label}`, async (data) => {
        const body = {name: data.get('name').trim(), active: data.has('active')};
        if (products) Object.assign(body, productValues(data));
        await api(`${path}/${resource(item.id)}`, {method: 'PATCH', body});
        Object.assign(item, body);
        summary.textContent = title();
        return 'Alterações salvas.';
      });
      return element('details', {class: 'edit-disclosure'}, summary, form);
    }));
    if (!records.length) list.append(muted(products ? 'Nenhum produto cadastrado.' : 'Nenhuma empresa disponível.'));
  }

  drawRecords();
  const create = canEdit ? panel(products ? 'Novo produto' : 'Nova empresa', actionForm([
    element('div', {class: 'form-grid'}, nameField(), slugField()),
    ...(products ? productFields() : []),
  ].filter(Boolean), `Cadastrar ${label}`, async (data, form) => {
    const body = {name: data.get('name').trim(), slug: data.get('slug')};
    if (products) Object.assign(body, productValues(data));
    records.unshift(await api(path, {method: 'POST', body}));
    drawRecords();
    form.reset();
    return products ? 'Produto cadastrado.' : 'Empresa cadastrada.';
  })) : null;

  screen.replaceChildren(...[create, panel(products ? 'Produtos cadastrados' : 'Empresas disponíveis', list)].filter(Boolean));
}

async function overview(canEdit) {
  const [companies, products] = await Promise.all([api(clientsPath), canEdit ? api(productsPath) : Promise.resolve([])]);
  const companiesPanel = panel('Empresas disponíveis',
    element('p', {class: 'metric'}, String(companies.length)),
    link('/admin/clientes', 'Ver empresas'),
  );
  const productsPanel = canEdit ? panel('Produtos no catálogo',
    element('p', {class: 'metric'}, String(products.length)),
    link('/admin/produtos', 'Ver catálogo'),
  ) : null;
  screen.replaceChildren(element('div', {class: 'split-panels'}, companiesPanel, productsPanel));
  if (canEdit) screen.append(panel('Acessos e ativações',
    link('/admin/acessos', 'Gerenciar acessos por usuário'),
    link('/admin/solicitacoes', 'Analisar solicitações de ferramentas')));
}

function localDateInput(value) {
  if (!value) return '';
  const instant = new Date(value);
  const adjusted = new Date(instant.getTime() - instant.getTimezoneOffset() * 60000);
  return adjusted.toISOString().slice(0, 16);
}

function datePayload(input, existing) {
  if (input === localDateInput(existing)) return existing ?? null;
  return input ? new Date(input).toISOString() : null;
}

async function licenses(target, company, signal) {
  const path = `${clientsPath}/${resource(company.id)}/licenses`;
  const [products, existing] = await Promise.all([api(productsPath, {signal}), api(path, {signal})]);
  if (signal.aborted) return;
  const granted = new Map(existing.map((item) => [item.product_id, item]));
  const productForms = products.map((product) => {
    let current = granted.get(product.id);
    const title = () => `${product.name} · ${current ? activeLabel(current.active) : 'Sem licença'}`;
    const summary = element('summary', {}, title());
    const periods = element('p', {class: 'muted'});
    const showPeriod = () => {
      const bounds = [current?.starts_at && `Início ${dateTime(current.starts_at)}`, current?.ends_at && `Término ${dateTime(current.ends_at)}`];
      periods.textContent = bounds.filter(Boolean).join(' · ');
      periods.hidden = !periods.textContent;
    };
    showPeriod();
    const form = actionForm([
      !product.active && notice('O produto está inativo no catálogo.'),
      checkField('Licença ativa', 'active', Boolean(current?.active)),
      element('div', {class: 'form-grid'},
        inputField('Início', 'starts_at', {type: 'datetime-local', value: localDateInput(current?.starts_at)}, 'Horário local. Campo opcional.'),
        inputField('Término', 'ends_at', {type: 'datetime-local', value: localDateInput(current?.ends_at)}, 'Horário local. Campo opcional.'),
      ),
    ].filter(Boolean), 'Salvar licença', async (data) => {
      const start = data.get('starts_at');
      const end = data.get('ends_at');
      const body = {
        active: data.has('active'),
        starts_at: datePayload(start, current?.starts_at),
        ends_at: datePayload(end, current?.ends_at),
      };
      if (body.starts_at && body.ends_at && new Date(body.starts_at) >= new Date(body.ends_at)) {
        throw new UserMessage('O término precisa ser posterior ao início.');
      }
      current = await api(`${path}/${resource(product.id)}`, {method: 'PUT', body, signal});
      form.elements.namedItem('starts_at').value = localDateInput(current.starts_at);
      form.elements.namedItem('ends_at').value = localDateInput(current.ends_at);
      summary.textContent = title();
      showPeriod();
      return 'Licença salva.';
    });
    return element('details', {class: 'edit-disclosure'}, summary, periods, form);
  });
  const content = productForms.length ? productForms : muted('Cadastre um produto para configurar licenças.');
  target.replaceChildren(panel(`Licenças de ${company.name}`, content));
}

async function companyManagement(canEdit) {
  const companies = await api(clientsPath);
  if (!companies.length) {
    screen.replaceChildren(notice('Nenhuma empresa disponível.'));
    return;
  }

  const options = [['', 'Selecione uma empresa'], ...companies.map((company) => [company.id, company.name])];
  const control = select(options);
  control.options[0].disabled = true;
  const target = element('div', {class: 'stack'});
  let loading;
  async function changeCompany() {
    loading?.abort();
    loading = new AbortController();
    const {signal} = loading;
    const company = companies.find((item) => item.id === control.value);
    if (!company) return;
    target.replaceChildren(notice('Carregando…'));
    try {
      if (view === 'members') {
        await members(target, `${clientsPath}/${resource(company.id)}/users`, {canEdit, canAssignAdmin: canEdit, signal});
      } else {
        await licenses(target, company, signal);
      }
    } catch (error) {
      if (!signal.aborted) target.replaceChildren(notice(error instanceof UserMessage ? error.message : 'Não foi possível consultar esta empresa.', true));
    }
  }
  control.addEventListener('change', () => void changeCompany());
  screen.replaceChildren(element('div', {class: 'tenant-picker'}, field('Empresa', control)), target);
  if (companies.length === 1) {
    control.value = companies[0].id;
    await changeCompany();
  }
}

void boot('admin', async (context) => {
  if (!isStaff(context)) {
    screen.replaceChildren(notice('Seu perfil não tem acesso à administração da FuturaNext.', true), link('/portal', 'Ir para o portal'));
    return;
  }

  const canEdit = context.platform_role === 'super_admin';
  const allowedPages = canEdit ? pages : pages.slice(0, 3);
  navigation(allowedPages);
  if (!allowedPages.some(([, , id]) => id === view)) {
    screen.replaceChildren(notice('Seu perfil não tem acesso a esta tela.', true));
  } else if (context.aal !== 'aal2') {
    await requireMfa(screen);
  } else if (view === 'overview') {
    await overview(canEdit);
  } else if (view === 'clients' || view === 'products' && canEdit) {
    await catalog(canEdit, view === 'products');
  } else if (view === 'access') {
    userAccess();
  } else if (view === 'requests') {
    await toolRequests();
  } else {
    await companyManagement(canEdit);
  }
});
