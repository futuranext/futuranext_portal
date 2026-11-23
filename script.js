(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const config = window.FUTURANEXT_CONFIG || {};
  const money = n => new Intl.NumberFormat('pt-BR', {style:'currency',currency:'BRL',maximumFractionDigits:0}).format(n);
  $('year').textContent = new Date().getFullYear();
  if (config.email) { const el = $('footerEmail'); el.textContent = config.email; }

  // Mobile navigation
  const toggle = $('menuToggle'), nav = $('mainNav');
  toggle.addEventListener('click', () => {
    const isOpen = nav.classList.toggle('open');
    toggle.setAttribute('aria-expanded', String(isOpen));
  });
  nav.querySelectorAll('a').forEach(a => a.addEventListener('click', () => {
    nav.classList.remove('open'); toggle.setAttribute('aria-expanded','false');
  }));

  // Cost estimator: illustrative 220h/month rate, actual task time 22 workdays.
  function updateCalc() {
    const people = +$('people').value, hours = +$('hours').value, salary = +$('salary').value;
    $('peopleValue').textContent = people;
    $('hoursValue').textContent = String(hours).replace('.', ',') + ' h';
    $('salaryValue').textContent = money(salary);
    $('monthlyResult').textContent = money(people * hours * 22 * (salary / 220));
  }
  ['people','hours','salary'].forEach(id => $(id).addEventListener('input',updateCalc));
  updateCalc();

  // Explicitly fictitious project scenarios, not client success claims.
  const cases = [
    {sector:'Financeiro',area:'Automação',name:'Conciliação de dados e relatórios',description:'Um fluxo que cruza registros, sinaliza divergências e organiza uma fila de exceções.',outcome:'Menos conferência manual e mais rastreabilidade.'},
    {sector:'Atendimento',area:'IA',name:'Assistente de atendimento via WhatsApp',description:'Recepção automática, qualificação inicial e encaminhamento contextualizado para humanos.',outcome:'Triagem e respostas padronizadas.'},
    {sector:'RH',area:'Sistemas',name:'Portal de candidatos e oportunidades',description:'Cadastro, organização de perfis e busca por competências em um único ambiente.',outcome:'Informação centralizada para o time.'},
    {sector:'Logística',area:'Integração',name:'Integração de pedidos e entregas',description:'Conexão entre sistemas, atualização de status e sinalização de eventos operacionais.',outcome:'Visibilidade do pedido ao destino.'},
    {sector:'Comercial',area:'IA',name:'Qualificação inteligente de contatos',description:'Fluxo para coletar informações, identificar a demanda e apoiar o atendimento comercial.',outcome:'Contatos mais bem organizados.'},
    {sector:'Financeiro',area:'Sistemas',name:'Dashboard de indicadores operacionais',description:'Painéis com métricas e filtros para acompanhar tarefas, resultados e tendências.',outcome:'Gestão orientada por dados.'},
    {sector:'RH',area:'Automação',name:'Rotinas de admissão e desligamento',description:'Automação de tarefas administrativas e acompanhamento de solicitações internas.',outcome:'Processos mais consistentes.'},
    {sector:'Logística',area:'Automação',name:'Monitoramento de exceções',description:'Consulta, classificação e escalonamento de ocorrências em uma fila monitorável.',outcome:'Menos retrabalho no acompanhamento.'},
    {sector:'Atendimento',area:'Integração',name:'Central unificada de solicitações',description:'Integração de canais e sistemas para organizar atendimentos em um portal único.',outcome:'Histórico consolidado de solicitações.'}
  ];
  const caseSearch=$('caseSearch'), caseSector=$('caseSector'), caseArea=$('caseArea'), grid=$('casesGrid');
  const normalized = str => str.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'');
  function renderCases() {
    const term=normalized(caseSearch.value.trim());
    const shown=cases.filter(c => (!caseSector.value || c.sector===caseSector.value) && (!caseArea.value || c.area===caseArea.value) && normalized(Object.values(c).join(' ')).includes(term));
    $('caseCount').textContent = `${shown.length} aplicaç${shown.length===1?'ão':'ões'} encontrada${shown.length===1?'':'s'}`;
    grid.replaceChildren();
    if (!shown.length) { const empty=document.createElement('p'); empty.className='empty-cases'; empty.textContent='Nenhuma aplicação encontrada. Tente outros filtros ou conte seu desafio no chat.';grid.appendChild(empty);return; }
    shown.forEach(c => {
      const node=$('caseTemplate').content.cloneNode(true);
      node.querySelector('.case-sector').textContent=c.sector;
      node.querySelector('.case-area').textContent=c.area;
      node.querySelector('h3').textContent=c.name;
      node.querySelector('.case-desc').textContent=c.description;
      node.querySelector('.case-outcome strong').textContent=c.outcome;
      node.querySelector('.case-button').addEventListener('click',()=>openChat(c.name));
      grid.appendChild(node);
    });
  }
  [caseSearch,caseSector,caseArea].forEach(el=>el.addEventListener('input',renderCases));
  renderCases();

  // Atendimento com IA: o navegador guarda só a conversa aberta e envia o histórico à API da FuturaNext,
  // que aplica o prompt fixo e os limites de uso. Nada é salvo neste navegador.
  const apiBase=(config.apiBase||'').replace(/\/$/,'');
  const contactEmail=config.email||'suporte@futuranext.com.br';
  const overlay=$('chatOverlay'), messages=$('chatMessages'), form=$('chatForm'), input=$('chatInput'), choices=$('chatChoices'), helper=$('chatHelper'), send=$('chatSend');
  const HELP='Respostas geradas por IA sobre a FuturaNext. Não compartilhe senhas, documentos ou dados de cartão.';
  const GREETING='Olá! 👋 Sou o assistente virtual da FuturaNext. Posso explicar nossas soluções, como funciona o diagnóstico gratuito e o teste de 15 dias da plataforma. Como posso ajudar?';
  const STARTERS=['Quais soluções vocês oferecem?','Como funciona o diagnóstico gratuito?','Quero testar a plataforma'];
  let history=[], busy=false, previousFocus=null, leadSent=false;
  function addMessage(role,text) {
    const el=document.createElement('div');el.className='chat-message '+role;
    const who=document.createElement('span');who.className='chat-role';who.textContent=role==='bot'?'FuturaNext':'Você';
    const bubble=document.createElement('div');bubble.className='chat-bubble';bubble.textContent=text;
    el.append(who,bubble);messages.appendChild(el);messages.scrollTop=messages.scrollHeight;
    return el;
  }
  function choice(label,fn,secondary=false) {
    const btn=document.createElement('button');btn.type='button';btn.textContent=label;
    if(secondary) btn.className='secondary-action';
    btn.addEventListener('click',fn);return btn;
  }
  function renderChoices() {
    const items=history.length?[]:STARTERS.map(text=>choice(text,()=>ask(text)));
    if(!leadSent) items.push(choice('Falar com a equipe',leadForm,true));
    choices.replaceChildren(...items);
  }
  async function post(path,body) {
    const response=await fetch(apiBase+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    let data=null;try{data=await response.json();}catch{data=null;}
    if(!response.ok) throw new Error(typeof data?.detail==='string'?data.detail:'Não foi possível concluir agora.');
    return data;
  }
  async function ask(text) {
    const value=text.trim();
    if(!value||busy) return;
    busy=true;send.disabled=true;input.value='';
    addMessage('user',value);
    history.push({role:'user',content:value.slice(0,1500)});
    history=history.slice(-24);
    while(history.length && history[0].role!=='user') history.shift();
    renderChoices();
    const typing=addMessage('bot','Digitando…');typing.classList.add('typing');
    try {
      const {reply}=await post('/api/v1/public/chat',{messages:history});
      typing.remove();addMessage('bot',reply);
      history.push({role:'assistant',content:reply.slice(0,1500)});
    } catch(error) {
      typing.remove();history.pop();
      addMessage('bot',(error instanceof TypeError?'Não consegui me conectar agora.':error.message)+` Se preferir, use "Falar com a equipe" ou escreva para ${contactEmail}.`);
    } finally {
      busy=false;send.disabled=false;input.focus();
    }
  }
  function leadField(label,name,attrs={}) {
    const wrap=document.createElement('label');wrap.className='lead-field';
    const span=document.createElement('span');span.textContent=label;
    const control=document.createElement(attrs.multiline?'textarea':'input');
    control.name=name;control.maxLength=attrs.max||150;control.required=!attrs.optional;
    if(attrs.autocomplete)control.autocomplete=attrs.autocomplete;
    if(attrs.multiline)control.rows=3;
    wrap.append(span,control);return wrap;
  }
  function leadForm() {
    const open=messages.querySelector('.lead-form');
    if(open) {open.querySelector('input').focus();return;}
    const box=document.createElement('form');box.className='chat-finished lead-form';
    const title=document.createElement('strong');title.textContent='Deixe seu contato e a equipe da FuturaNext retorna para você.';
    const consent=document.createElement('label');consent.className='lead-consent';
    const check=document.createElement('input');check.type='checkbox';check.name='consent';check.required=true;
    consent.append(check,document.createTextNode(' Autorizo a FuturaNext a usar estes dados e esta conversa para entrar em contato sobre minha solicitação.'));
    const status=document.createElement('p');status.className='lead-status';status.setAttribute('role','status');
    const submit=document.createElement('button');submit.type='submit';submit.textContent='Enviar para a equipe';
    box.append(title,
      leadField('Nome','name',{max:100,autocomplete:'name'}),
      leadField('Empresa','company',{max:120,autocomplete:'organization'}),
      leadField('E-mail ou WhatsApp com DDD','contact',{max:150,autocomplete:'email'}),
      leadField('Como podemos ajudar? (opcional)','message',{max:1500,optional:true,multiline:true}),
      consent,submit,status);
    box.addEventListener('submit',async e=>{
      e.preventDefault();
      const data=new FormData(box);
      const contact=String(data.get('contact')).trim();
      if(!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(contact)&&contact.replace(/\D/g,'').length<10){status.textContent='Informe um e-mail válido ou telefone com DDD.';return;}
      submit.disabled=true;status.textContent='Enviando…';
      try {
        await post('/api/v1/public/leads',{name:String(data.get('name')).trim(),company:String(data.get('company')).trim(),contact,
          message:String(data.get('message')||'').trim(),consent:check.checked,transcript:history.slice(-24)});
        box.remove();leadSent=true;renderChoices();
        addMessage('bot','Recebemos seu contato! A equipe da FuturaNext vai retornar em breve. Se quiser, continue perguntando por aqui.');
      } catch(error) {
        submit.disabled=false;
        status.textContent=(error instanceof TypeError?'Sem conexão no momento.':error.message)+` Você também pode escrever para ${contactEmail}.`;
      }
    });
    messages.appendChild(box);messages.scrollTop=messages.scrollHeight;
    box.querySelector('input').focus();
  }
  function resetChat() {
    history=[];busy=false;leadSent=false;send.disabled=false;
    messages.replaceChildren();input.value='';helper.textContent=HELP;
    addMessage('bot',GREETING);renderChoices();
  }
  function openChat(topic='') {
    previousFocus=document.activeElement;
    overlay.hidden=false;document.body.classList.add('chat-open');
    if(!messages.childElementCount) resetChat();
    if(topic) input.value=`Tenho interesse em algo parecido com: ${topic}.`;
    input.focus();
  }
  function closeChat() {
    overlay.hidden=true;document.body.classList.remove('chat-open');
    if(previousFocus && typeof previousFocus.focus==='function') previousFocus.focus();
  }
  form.addEventListener('submit', e=>{e.preventDefault();void ask(input.value);});
  input.addEventListener('keydown',e=>{
    if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();if(input.value.trim())form.requestSubmit();}
  });
  document.querySelectorAll('.js-open-chat').forEach(b=>b.addEventListener('click',()=>openChat()));
  $('chatClose').addEventListener('click',closeChat);
  $('chatRestart').addEventListener('click',()=>{if(history.length&&!window.confirm('Recomeçar a conversa e apagar as mensagens atuais?'))return;resetChat();});
  overlay.addEventListener('click',e=>{if(e.target===overlay)closeChat();});
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'&&!overlay.hidden)closeChat();
    if(e.key==='Tab'&&!overlay.hidden){const tabbable=[...overlay.querySelectorAll('button:not([hidden]):not(:disabled),textarea:not([disabled]),input:not([disabled])')].filter(el=>el.getClientRects().length);if(!tabbable.length)return;const first=tabbable[0],last=tabbable[tabbable.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}
  });
})();
