import {api, ApiError, resource} from '../client.js';
import {
  actionForm, button, checkField, element, field, focusHeading, inputField,
  muted, notice, panel, runAction, select, statusMessage, UserMessage,
} from '../ui.js';

const timeZone = 'America/Sao_Paulo';
const localParts = new Intl.DateTimeFormat('en-CA', {
  timeZone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});
const displayDate = new Intl.DateTimeFormat('pt-BR', {timeZone: 'UTC', weekday: 'long', day: '2-digit', month: 'long'});
const currency = new Intl.NumberFormat('pt-BR', {style: 'currency', currency: 'BRL'});
const weekdays = ['Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira', 'Sexta-feira', 'Sábado', 'Domingo'];
const statusLabel = {
  scheduled: 'Agendado', confirmed: 'Confirmado', cancelled: 'Cancelado',
  completed: 'Concluído', no_show: 'Falta', blocked: 'Bloqueado',
};

function localInput(value) {
  const parts = Object.fromEntries(localParts.formatToParts(new Date(value)).map(({type, value: part}) => [type, part]));
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

function shiftDate(day, days) {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function describeTime(slot, shownDay) {
  const start = localInput(slot.starts_at);
  const finish = localInput(slot.ends_at);
  const shownTime = (stamp) => {
    const day = stamp.slice(0, 10);
    const hours = stamp.slice(11);
    return day === shownDay ? hours : `${displayDate.format(new Date(`${day}T00:00:00Z`))}, ${hours}`;
  };
  return `${shownTime(start)}–${shownTime(finish)}`;
}

function optionsField(label, name, options, selected = '') {
  return field(label, select([['', 'Selecione'], ...options], {name, required: true}, selected));
}

function datetimeField(label, name, value) {
  return inputField(label, name, {type: 'datetime-local', value, required: true});
}

const pairs = (items) => items.map(({id, name}) => [id, name]);
const dayOptions = weekdays.map((day, index) => [String(index), day]);
const state = (active) => active ? 'Ativo' : 'Inativo';

function disclosure(title, ...content) {
  return element('details', {class: 'edit-disclosure'}, element('summary', {}, title), ...content);
}

function listOrEmpty(items, text) {
  return items.length ? element('div', {class: 'stack compact'}, items) : muted(text);
}

function createIdempotency() {
  let previous;
  let requestId;
  return (payload) => {
    const serialized = JSON.stringify(payload);
    if (previous !== serialized) {
      requestId = crypto.randomUUID();
      previous = serialized;
    }
    return {...payload, request_id: requestId};
  };
}

function appointmentForm(professionals, services, save) {
  const deduplicate = createIdempotency();
  return actionForm([
    element('div', {class: 'form-grid'},
      optionsField('Profissional', 'professional_id', pairs(professionals)),
      optionsField('Serviço', 'service_id', services.map((item) => [item.id, `${item.name} · ${item.duration_minutes} min`])),
      datetimeField('Início', 'starts_at'),
      inputField('Primeiro nome ou apelido', 'client_name', {maxlength: 120, required: true}),
    ),
  ], 'Registrar atendimento', (data) => save('/appointments', {
    method: 'POST', body: deduplicate(Object.fromEntries(data)),
  }, 'Atendimento agendado.', 'Confira se o atendimento cabe no expediente e se o período está disponível.'));
}

function blockForm(professionals, save) {
  const deduplicate = createIdempotency();
  return actionForm([
    element('div', {class: 'form-grid'},
      optionsField('Profissional', 'professional_id', pairs(professionals)),
      datetimeField('Início', 'starts_at'), datetimeField('Término', 'ends_at'),
      inputField('Motivo do bloqueio', 'label', {maxlength: 120, required: true}, 'Use uma descrição curta, como feriado ou folga.'),
    ),
  ], 'Registrar bloqueio', (data) => {
    if (data.get('ends_at') <= data.get('starts_at')) throw new UserMessage('O término precisa ser posterior ao início.');
    return save('/blocks', {method: 'POST', body: deduplicate(Object.fromEntries(data))},
      'Período bloqueado.', 'O bloqueio coincide com um atendimento ou outro bloqueio.');
  });
}

function slotActions(slot, save) {
  if (slot.kind === 'block' && slot.status === 'blocked') {
    const cancel = button('Remover bloqueio', true);
    const status = statusMessage();
    cancel.addEventListener('click', runAction(cancel, status, () => save(
      `/blocks/${resource(slot.id)}/cancel`, {method: 'POST'}, 'Bloqueio removido.',
    )));
    return element('div', {class: 'stack compact'}, cancel, status);
  }
  if (slot.kind !== 'appointment' || !['scheduled', 'confirmed'].includes(slot.status)) return null;

  const path = `/appointments/${resource(slot.id)}`;
  const states = [['cancelled', 'Cancelar']];
  if (slot.status === 'scheduled') states.unshift(['confirmed', 'Confirmar']);
  if (new Date(slot.starts_at) <= new Date()) states.push(['completed', 'Concluir'], ['no_show', 'Registrar falta']);
  return element('div', {class: 'stack'},
    actionForm([optionsField('Mudar estado', 'status', states)], 'Salvar estado', (data) => save(path,
      {method: 'PATCH', body: {status: data.get('status')}}, 'Estado atualizado.',
      'Este atendimento mudou de estado. Atualize a agenda e tente novamente.',
    )),
    actionForm([datetimeField('Novo início', 'starts_at', localInput(slot.starts_at))], 'Remarcar', (data) => save(path,
      {method: 'PATCH', body: {starts_at: data.get('starts_at')}}, 'Atendimento remarcado.',
      'Confira o expediente e outros atendimentos ou bloqueios nesse período.',
    )),
  );
}

function agendaList(model, canEdit, save) {
  const professionals = new Map(pairs(model.professionals));
  const services = new Map(pairs(model.services));
  const sections = new Map();
  for (const slot of model.slots) {
    const day = localInput(slot.starts_at).slice(0, 10);
    const firstDay = day < model.range_start ? model.range_start : day;
    if (!sections.has(firstDay)) sections.set(firstDay, []);

    const stateLabel = slot.kind === 'block' && slot.status === 'cancelled' ? 'Bloqueio removido' : statusLabel[slot.status];
    const title = `${describeTime(slot, firstDay)} · ${slot.label} · ${stateLabel}`;
    const metadata = [professionals.get(slot.professional_id)];
    if (slot.service_id) metadata.push(services.get(slot.service_id), currency.format(slot.service_price_cents / 100));
    const actions = canEdit && slotActions(slot, save);
    sections.get(firstDay).push(actions
      ? disclosure(title, muted(metadata.filter(Boolean).join(' · ')), actions)
      : element('article', {class: 'record'}, element('strong', {}, title), muted(metadata.filter(Boolean).join(' · '))));
  }

  return Array.from(sections, ([day, slots]) => panel(displayDate.format(new Date(`${day}T00:00:00Z`)), ...slots));
}

function scheduleView(model, canEdit, load, save) {
  const start = model.range_start;
  const end = shiftDate(model.range_end, -1);
  const from = inputField('De', 'from', {type: 'date', value: start, required: true});
  const to = inputField('Até', 'to', {type: 'date', value: end, required: true});
  const rangeForm = actionForm([element('div', {class: 'form-grid'}, from, to)], 'Aplicar período', (values) => {
    const first = values.get('from');
    const last = values.get('to');
    if (first > last || last >= shiftDate(first, 31)) throw new UserMessage('Escolha um período de 1 a 31 dias.');
    return load(first, shiftDate(last, 1));
  });

  const duration = (Date.parse(model.range_end) - Date.parse(start)) / 86400000;
  const periods = element('div', {class: 'form-actions'});
  for (const [label, days] of [['Período anterior', -duration], ['Próximo período', duration]]) {
    const control = button(label, true);
    const status = statusMessage();
    control.addEventListener('click', runAction(control, status, () => load(shiftDate(start, days), shiftDate(model.range_end, days))));
    periods.append(control, status);
  }

  const attention = model.metrics;
  const metrics = element('div', {class: 'product-grid'}, [
    ['Agendados', attention.scheduled], ['Confirmados', attention.confirmed], ['Concluídos', attention.completed],
    ['Cancelados', attention.cancelled], ['Faltas', attention.no_show], ['Bloqueios', attention.blocked],
    ['Valor dos serviços concluídos', currency.format(attention.completed_amount_cents / 100)],
  ].map(([label, value]) => element('article', {class: 'product-card'}, element('strong', {}, String(value)), muted(label))));

  const professionals = model.professionals.filter(({active}) => active);
  const services = model.services.filter(({active}) => active);
  const create = canEdit ? panel('Novo registro', muted('Os horários usam o fuso de São Paulo.'),
    professionals.length && services.length && model.hours.some((item) => item.active)
      ? disclosure('Agendar atendimento', appointmentForm(professionals, services, save))
      : muted('Cadastre profissional, serviço e expediente ativos para agendar.'),
    professionals.length ? disclosure('Bloquear horário', blockForm(professionals, save)) : null,
  ) : null;
  const entries = agendaList(model, canEdit, save);
  return [
    panel('Período da agenda', rangeForm, periods),
    panel('Resumo do período', metrics), create,
    model.truncated ? notice('A lista mostra os primeiros 600 registros. Reduza o período para consultar os demais.') : null,
    ...(entries.length ? entries : [panel('Horários registrados', muted('Não há registros no período selecionado.'))]),
  ];
}

function professionalForm(item, save) {
  const fields = [inputField('Nome', 'name', {value: item?.name, maxlength: 100, minlength: 2, required: true})];
  if (item) fields.push(checkField('Profissional ativo', 'active', item.active));
  return actionForm(fields, item ? 'Salvar profissional' : 'Cadastrar profissional', (data) => save(
    item ? `/professionals/${resource(item.id)}` : '/professionals',
    {method: item ? 'PATCH' : 'POST', body: {name: data.get('name').trim(), active: item ? data.has('active') : true}},
    'Profissional salvo.',
  ));
}

function priceField(item) {
  return inputField('Preço em reais', 'price', {
    type: 'number', min: 0, max: 999999.99, step: '.01', value: item == null ? '' : item.price_cents / 100, required: true,
  });
}

function serviceForm(item, save) {
  const fields = [
    element('div', {class: 'form-grid'},
      inputField('Nome', 'name', {value: item?.name, maxlength: 120, minlength: 2, required: true}),
      inputField('Duração em minutos', 'duration_minutes', {type: 'number', min: 5, max: 480, value: item?.duration_minutes, required: true}),
      priceField(item),
    ),
  ];
  if (item) fields.push(checkField('Serviço ativo', 'active', item.active));
  return actionForm(fields, item ? 'Salvar serviço' : 'Cadastrar serviço', (data) => save(
    item ? `/services/${resource(item.id)}` : '/services',
    {method: item ? 'PATCH' : 'POST', body: {
      name: data.get('name').trim(), duration_minutes: Number(data.get('duration_minutes')),
      price_cents: Math.round(Number(data.get('price')) * 100), active: item ? data.has('active') : true,
    }}, 'Serviço salvo.',
  ));
}

function offeringForm(item, save) {
  const fields = [
    element('div', {class: 'form-grid'},
      inputField('Nome', 'name', {value: item?.name, maxlength: 120, minlength: 2, required: true}),
      priceField(item),
    ),
    field('Descrição (opcional)', element('textarea', {name: 'description', maxlength: 600, rows: 3}, item?.description ?? '')),
  ];
  if (item) fields.push(checkField('Produto ativo', 'active', item.active));
  return actionForm(fields, item ? 'Salvar produto' : 'Cadastrar produto', (data) => save(
    item ? `/offerings/${resource(item.id)}` : '/offerings',
    {method: item ? 'PATCH' : 'POST', body: {
      name: data.get('name').trim(), description: data.get('description').trim(),
      price_cents: Math.round(Number(data.get('price')) * 100), active: item ? data.has('active') : true,
    }}, 'Produto salvo.',
  ));
}

function hoursForm(item, professionals, save) {
  const fields = [element('div', {class: 'form-grid'},
    item ? null : optionsField('Profissional', 'professional_id', pairs(professionals)),
    optionsField('Dia da semana', 'weekday', dayOptions, item ? String(item.weekday) : ''),
    inputField('Abertura', 'opens_at', {type: 'time', value: item?.opens_at.slice(0, 5), required: true}),
    inputField('Fechamento', 'closes_at', {type: 'time', value: item?.closes_at.slice(0, 5), required: true}),
  )];
  if (item) fields.push(checkField('Expediente ativo', 'active', item.active));
  return actionForm(fields, item ? 'Salvar expediente' : 'Cadastrar expediente', (data) => {
    const opens = data.get('opens_at');
    const closes = data.get('closes_at');
    if (opens >= closes) throw new UserMessage('O fechamento deve ser posterior à abertura, no mesmo dia.');
    const body = {weekday: Number(data.get('weekday')), opens_at: opens, closes_at: closes, active: item ? data.has('active') : true};
    if (!item) body.professional_id = data.get('professional_id');
    return save(item ? `/hours/${resource(item.id)}` : '/hours', {method: item ? 'PATCH' : 'POST', body},
      'Expediente salvo.', 'O horário se sobrepõe a outro expediente ou deixaria um atendimento fora do expediente.');
  });
}

function catalogView(model, canEdit, save) {
  const professionals = new Map(pairs(model.professionals));
  const professionalRows = model.professionals.map((item) => {
    const label = `${item.name} · ${state(item.active)}`;
    return canEdit ? disclosure(label, professionalForm(item, save)) : muted(label);
  });
  const serviceRows = model.services.map((item) => {
    const label = `${item.name} · ${item.duration_minutes} min · ${currency.format(item.price_cents / 100)} · ${state(item.active)}`;
    return canEdit ? disclosure(label, serviceForm(item, save)) : muted(label);
  });
  const productRows = (model.offerings ?? []).map((item) => {
    const label = `${item.name} · ${currency.format(item.price_cents / 100)} · ${state(item.active)}`;
    if (canEdit) return disclosure(label, offeringForm(item, save));
    return element('div', {class: 'record'}, element('strong', {}, label), item.description && muted(item.description));
  });
  const hourRows = model.hours.map((item) => {
    const label = `${professionals.get(item.professional_id)} · ${weekdays[item.weekday]} · ${item.opens_at.slice(0, 5)}–${item.closes_at.slice(0, 5)} · ${state(item.active)}`;
    return canEdit ? disclosure(label, hoursForm(item, model.professionals, save)) : muted(label);
  });
  return [
    panel('Profissionais', canEdit ? disclosure('Novo profissional', professionalForm(null, save)) : null,
      listOrEmpty(professionalRows, 'Nenhum profissional cadastrado.')),
    panel('Serviços', canEdit ? disclosure('Novo serviço', serviceForm(null, save)) : null,
      listOrEmpty(serviceRows, 'Nenhum serviço cadastrado.')),
    panel('Produtos oferecidos', canEdit ? disclosure('Novo produto', offeringForm(null, save)) : null,
      listOrEmpty(productRows, 'Nenhum produto oferecido cadastrado.')),
    panel('Expediente semanal', muted('Cadastre faixas separadas no mesmo dia para representar pausas.'),
      canEdit && model.professionals.length ? disclosure('Novo horário', hoursForm(null, model.professionals, save)) : null,
      listOrEmpty(hourRows, 'Nenhum horário cadastrado.')),
  ];
}

function conversationView(config, canEdit, save) {
  const messages = [
    ['greeting', 'Saudação'], ['confirmation', 'Confirmação'], ['cancellation', 'Cancelamento'],
    ['handoff', 'Encaminhamento à equipe'], ['faq', 'Perguntas frequentes'],
  ];
  const numbers = [
    ['minimum_notice_minutes', 'Antecedência para agendar (min)', 0, 10080],
    ['booking_horizon_days', 'Período máximo para agendar (dias)', 1, 365],
    ['cancellation_notice_minutes', 'Antecedência para cancelamento automático futuro (min)', 0, 10080],
  ];
  const intro = muted('Os textos serão usados na futura integração do chatbot. Use apenas informações gerais do estabelecimento.');
  const details = canEdit ? actionForm([
    ...messages.map(([key, label]) => field(label, element('textarea', {name: key, rows: 3, maxlength: key === 'faq' ? 2000 : 600}, config[key]))),
    element('div', {class: 'form-grid'}, numbers.map(([key, label, min, max]) => inputField(label, key, {
      type: 'number', min, max, value: config[key], required: true,
    }))),
  ], 'Salvar configurações', (data) => {
    const body = Object.fromEntries(data);
    for (const [key] of numbers) body[key] = Number(body[key]);
    return save('/conversation', {method: 'PUT', body}, 'Configurações salvas.');
  }) : element('div', {class: 'stack compact'}, [...messages, ...numbers].map(([key, label]) => (
    element('div', {}, element('strong', {}, label), muted(config[key] === '' ? 'Sem texto cadastrado.' : String(config[key])))
  )));
  return [panel('Mensagens e regras', intro, details)];
}

export async function renderAgenda(target, {tenant, tool, signal, canEdit}) {
  const base = `/tenants/${resource(tenant.id)}/tools/${resource(tool.slug)}/agenda`;
  let model = await api(base, {signal});
  if (signal?.aborted) return;

  let selected = 'agenda';
  let revision = 0;
  const content = element('div', {class: 'stack'});
  const feedback = statusMessage();
  const tabs = [['agenda', 'Agenda'], ['catalog', 'Cadastros'], ['conversation', 'Configuração de conversa']];
  const navigation = element('nav', {class: 'form-actions', 'aria-label': 'Seções da agenda'});
  const controls = new Map(tabs.map(([key, label]) => {
    const tab = button(label, true);
    tab.addEventListener('click', () => {
      selected = key;
      draw();
      focusHeading(content);
    });
    navigation.append(tab);
    return [key, tab];
  }));

  async function load(first, last) {
    const current = ++revision;
    const query = new URLSearchParams({from: first, to: last});
    const next = await api(`${base}?${query}`, {signal});
    if (signal?.aborted || current !== revision) return;
    model = next;
    draw();
  }

  async function save(path, options, message, conflict) {
    let result;
    try {
      result = await api(`${base}${path}`, {...options, signal});
    } catch (error) {
      if (conflict && error instanceof ApiError && error.status === 409) throw new UserMessage(conflict);
      throw error;
    }
    if (signal?.aborted) return;
    const day = result.starts_at && localInput(result.starts_at).slice(0, 10);
    const outside = day && (day < model.range_start || day >= model.range_end);
    feedback.className = 'status-message success';
    feedback.textContent = message;
    try {
      await load(outside ? day : model.range_start, outside ? shiftDate(day, 7) : model.range_end);
    } catch {
      if (signal?.aborted) return;
      feedback.className = 'status-message error';
      feedback.textContent = `${message} Atualize a agenda para ver os dados atuais.`;
    }
  }

  function draw() {
    for (const [key, control] of controls) {
      const active = key === selected;
      control.setAttribute('aria-pressed', String(active));
      control.className = active ? 'button' : 'button secondary';
    }
    const sections = selected === 'catalog' ? catalogView(model, canEdit, save)
      : selected === 'conversation' ? conversationView(model.conversation, canEdit, save)
        : scheduleView(model, canEdit, load, save);
    content.replaceChildren(...sections.filter(Boolean));
  }

  target.replaceChildren(navigation, feedback, content);
  draw();
}
