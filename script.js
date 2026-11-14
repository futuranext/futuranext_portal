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
    $('caseCount').textContent = `${shown.length} exemplo${shown.length===1?'':'s'} encontrado${shown.length===1?'':'s'}`;
    grid.replaceChildren();
    if (!shown.length) { const empty=document.createElement('p'); empty.className='empty-cases'; empty.textContent='Nenhum exemplo encontrado. Tente outros filtros ou conte seu desafio no chat.';grid.appendChild(empty);return; }
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

  // Guided chat, no external AI or backend. All steps execute locally.
  const steps = [
    {id:'name',prompt:'Olá! 👋 Que bom ter você aqui. Sou o assistente de pré-atendimento da FuturaNext. Para começar, qual é seu nome?',placeholder:'Seu nome...',max:100,validate:v=>v.length>=2?'':'Informe seu nome com pelo menos 2 caracteres.'},
    {id:'company',prompt:v=>`Prazer, ${v.name.split(' ')[0]}! Qual é o nome da sua empresa ou projeto?`,placeholder:'Empresa ou projeto...',max:120,validate:v=>v.length>=2?'':'Informe a empresa ou projeto.'},
    {id:'sector',prompt:'Em qual área podemos ajudar?',choices:['Automação RPA','Inteligência artificial','Sistema ou portal web','Integrações e APIs','Dashboards e dados','Ainda não sei'],max:100},
    {id:'problem',prompt:'Agora me conte o principal: qual problema você precisa resolver? Pode explicar com suas palavras e dar exemplos do processo atual.',placeholder:'Descreva a necessidade do seu negócio...',max:1500,validate:v=>v.length>=12?'':'Descreva um pouco mais (mínimo 12 caracteres).'},
    {id:'priority',prompt:'Qual é a urgência aproximada desse projeto?',choices:['O quanto antes','Nos próximos 30 dias','Nos próximos 3 meses','Ainda estou planejando'],max:100},
    {id:'contact',prompt:'Qual é o melhor e-mail ou WhatsApp para entrarmos em contato? (Digite e-mail ou telefone com DDD.)',placeholder:'E-mail ou WhatsApp com DDD...',max:150,validate:v=>/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)||v.replace(/\D/g,'').length>=10?'':'Informe um e-mail válido ou telefone com DDD (10 ou mais dígitos).'},
    {id:'consent',prompt:'Para concluir: você autoriza que os dados informados sejam usados para o contato e avaliação da sua solicitação? Nesta versão de teste os dados ficam apenas neste navegador, até você escolher exportar ou compartilhá-los.',choices:['Sim, autorizo','Não autorizo'],max:30}
  ];
  const overlay=$('chatOverlay'), messages=$('chatMessages'), form=$('chatForm'), input=$('chatInput'), choices=$('chatChoices'), progress=$('chatProgress'), helper=$('chatHelper');
  let index=0, values={}, completed=false, previousFocus=null;
  function addMessage(role,text) {
    const el=document.createElement('div');el.className='chat-message '+role;
    const who=document.createElement('span');who.className='chat-role';who.textContent=role==='bot'?'FuturaNext':'Você';
    const bubble=document.createElement('div');bubble.className='chat-bubble';bubble.textContent=text;
    el.append(who,bubble);messages.appendChild(el);messages.scrollTop=messages.scrollHeight;
  }
  function question() {
    const step=steps[index];
    if (!step) return;
    const msg=typeof step.prompt==='function'?step.prompt(values):step.prompt;
    addMessage('bot',msg);
    input.value='';input.maxLength=step.max||1500;input.placeholder=step.placeholder||'Escreva sua resposta ou escolha uma opção...';
    choices.replaceChildren();
    if(step.choices) step.choices.forEach(choice=>{
      const btn=document.createElement('button');btn.type='button';btn.textContent=choice;
      btn.addEventListener('click',()=>submitAnswer(choice));choices.appendChild(btn);
    });
    progress.style.width=(index/steps.length*100)+'%';
    input.focus();
  }
  function resetChat(prefill='') {
    index=0;values={};completed=false;messages.replaceChildren();choices.replaceChildren();
    form.hidden=false;helper.textContent='Conversa guiada local: nenhuma mensagem é enviada automaticamente.';
    question();
    if(prefill) {
      // Suggest scenario only after the first actual conversation question.
      values.suggestedCase=prefill;
    }
  }
  function openChat(prefill='') {
    previousFocus=document.activeElement;
    overlay.hidden=false;document.body.classList.add('chat-open');
    resetChat(prefill);
  }
  function closeChat() {
    overlay.hidden=true;document.body.classList.remove('chat-open');
    if(previousFocus && typeof previousFocus.focus==='function') previousFocus.focus();
  }
  function submitAnswer(raw) {
    if(completed) return;
    const step=steps[index], v=raw.trim();
    if(!v) { input.focus();return; }
    const error=step.validate?.(v);
    if(error) {helper.textContent=error;input.focus();return;}
    helper.textContent='Conversa guiada local: nenhuma mensagem é enviada automaticamente.';
    addMessage('user',v);
    if(step.id==='consent' && v==='Não autorizo') {
      addMessage('bot','Tudo bem. Seu atendimento não será encaminhado e não vou criar um resumo para compartilhamento. Você pode fechar a janela ou recomeçar.');
      completed=true;form.hidden=true;progress.style.width='100%';return;
    }
    values[step.id]=v;
    index++;
    if(index<steps.length) question(); else finishChat();
  }
  function buildSummary() {
    const rows=[
      'FUTURANEXT | NOVA SOLICITAÇÃO (PRÉ-ATENDIMENTO)',
      `Data: ${new Date().toLocaleString('pt-BR')}`,
      `Nome: ${values.name}`,
      `Empresa/projeto: ${values.company}`,
      `Área: ${values.sector}`,
      `Necessidade: ${values.problem}`,
      `Prazo: ${values.priority}`,
      `Contato: ${values.contact}`,
      `Autorização para contato: ${values.consent}`
    ];
    if(values.suggestedCase) rows.push(`Referência de interesse: ${values.suggestedCase}`);
    return rows.join('\n');
  }
  function action(label,fn,secondary=false) {
    const btn=document.createElement('button');btn.type='button';btn.textContent=label;
    if(secondary) btn.className='secondary-action';btn.addEventListener('click',fn);return btn;
  }
  function finishChat() {
    completed=true;progress.style.width='100%';form.hidden=true;
    addMessage('bot',`Obrigado, ${values.name.split(' ')[0]}! Seu resumo está pronto. Esta versão é demonstrativa: ainda não existe envio automático para a FuturaNext. Escolha uma das opções abaixo para salvar ou encaminhar sua solicitação.`);
    const container=document.createElement('div');container.className='chat-finished';
    container.appendChild(action('Baixar resumo (.txt)',()=>{
      const blob=new Blob([buildSummary()],{type:'text/plain;charset=utf-8'});
      const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='futuranext-solicitacao.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }));
    container.appendChild(action('Copiar resumo',async()=>{
      try {await navigator.clipboard.writeText(buildSummary());helper.textContent='Resumo copiado.';}
      catch {helper.textContent='Não foi possível copiar automaticamente; use Baixar resumo.';}
    },true));
    const phone=(config.whatsapp||'').replace(/\D/g,'');
    if(phone.length>=12) container.appendChild(action('Enviar via WhatsApp',()=>{
      window.open('https://wa.me/'+phone+'?text='+encodeURIComponent(buildSummary()),'_blank','noopener,noreferrer');
    }));
    if(config.email && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(config.email)) container.appendChild(action('Enviar via e-mail',()=>{
      window.location.href='mailto:'+config.email+'?subject='+encodeURIComponent('Novo diagnóstico - FuturaNext')+'&body='+encodeURIComponent(buildSummary());
    },true));
    container.appendChild(action('Nova conversa',()=>resetChat(),true));
    messages.appendChild(container);messages.scrollTop=messages.scrollHeight;
    helper.textContent='Sem envio automático ou armazenamento remoto. O compartilhamento depende da sua ação.';
  }
  form.addEventListener('submit', e=>{e.preventDefault();submitAnswer(input.value);});
  input.addEventListener('keydown',e=>{
    if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();if(input.value.trim())form.requestSubmit();}
  });
  document.querySelectorAll('.js-open-chat').forEach(b=>b.addEventListener('click',()=>openChat()));
  $('chatClose').addEventListener('click',closeChat);
  $('chatRestart').addEventListener('click',()=>{if(!completed&&index>0&&!window.confirm('Recomeçar o diagnóstico e descartar as respostas atuais?'))return;resetChat();});
  overlay.addEventListener('click',e=>{if(e.target===overlay)closeChat();});
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'&&!overlay.hidden)closeChat();
    if(e.key==='Tab'&&!overlay.hidden){const tabbable=[...overlay.querySelectorAll('button:not([hidden]):not(:disabled),textarea:not([disabled])')].filter(el=>el.getClientRects().length);if(!tabbable.length)return;const first=tabbable[0],last=tabbable[tabbable.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}
  });
})();
