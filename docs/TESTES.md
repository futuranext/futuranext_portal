# Testes e evidências

## Fase 0 · 14/11/2026

| Verificação | Resultado |
| --- | --- |
| Inventário de `README.md`, HTML, CSS, dois scripts e dois SVGs | Arquivos lidos e hashes de referência registrados. [Diagnóstico](DIAGNOSTICO_ATUAL.md) |
| Sintaxe de `script.js` e `config.js`, Node.js v24.16.0 | Ambos com código de saída 0. |
| Ensaio de lógica por `node:vm` com DOM simulado | Passaram menu, `aria-expanded`, calculadora, nove cases, filtros combinados, validação de nome, autorização e recusa, cópia simulada do resumo, referência de case e retorno de foco. |
| Landing em navegador real | Chrome headless não gerou DOM no sandbox e o navegador integrado bloqueou o protocolo `file:`. Uma tentativa de HTTP local recebeu `WinError 10013`. A verificação visual e das APIs reais de download e clipboard ficou pendente. |
| Python e Compose | Python 3.13 instalado no host. Docker CLI 28.0.4 e Compose 2.34.0 disponíveis. `docker version` recebeu `Access is denied` no pipe do Docker Desktop neste ambiente. |

```powershell
node --check script.js
node --check config.js
node docs/fixtures/landing-dom-smoke.cjs
Get-FileHash -Algorithm SHA256 -LiteralPath README.md,config.js,index.html,script.js,styles.css,assets/logo-mark.svg,assets/favicon.svg
py -0p
node --version
docker version --format '{{.Client.Version}} {{.Server.Version}}'
docker compose version
```

O ensaio usa apenas módulos nativos do Node.js, roda na raiz do projeto e emprega entradas sintéticas. As asserções verificam a lógica JavaScript, com simulação de eventos, controles, foco e clipboard.

Para reproduzir a verificação da landing em um ambiente com loopback disponível, execute `python -m http.server 5500 --bind 127.0.0.1` na raiz e abra `http://127.0.0.1:5500/`. A página também pode ser aberta diretamente no navegador do operador. Use entradas sintéticas no chat e mantenha a configuração comercial entregue para os testes locais.

1. Verifique a composição em larguras de 390, 768 e 1.440 px. Em 390 px, expanda o menu, siga o link Soluções e confirme o fechamento do menu.
2. Confirme `R$ 2.800` na calculadora. Mantendo os demais valores iniciais, ajuste horas para 2,5 e confirme `R$ 3.500`.
3. Verifique os nove cases iniciais e a indicação de conteúdo ilustrativo. Busque `conciliacao` sem acento, selecione `Financeiro` e `Automação`, e confira a contagem de um exemplo. Troque o segmento para `RH` e confira zero. Verifique os três depoimentos demonstrativos e a navegação para Quem Somos.
4. Acione o CTA de um case. Confira que um nome de um caractere gera mensagem de validação. Preencha o roteiro com dados fictícios, como `Pessoa Teste`, `Empresa Teste` e `teste@example.invalid`. Confirme que `Não autorizo` encerra o roteiro sem oferecer resumo.
5. Reinicie, preencha novamente, aceite pelo botão `Sim, autorizo` e confira o título do case em `Baixar resumo (.txt)` e `Copiar resumo`. Confira que os botões de envio via canal permanecem ocultos com a configuração recebida. Feche com `Escape` e verifique a restauração do foco ao CTA. Teste a navegação por teclado no diálogo.

## Fase 1 · Verificações executadas

| Execução | Resultado |
| --- | --- |
| `python -m pytest backend/tests/test_auth.py backend/tests/test_api.py -q` | 17 testes passaram. Incluem verificação JWT, estados do adaptador Auth, OpenAPI, CORS, allowlist de arquivos e tamanho de requisição. |
| `python -m pytest backend/tests/test_postgres.py -q --tb=short` | 9 testes passaram no PostgreSQL 15 descartável, com role real de runtime. Cobrem leitura e escrita entre empresas, produto sem licença ou permissão, suporte, MFA, convites, auditoria e reuso de conexão. |
| `python -m pytest backend/tests/test_local_auth_flow.py -q --tb=short` com `FN_RUN_LOCAL_AUTH=1` | Passou no Supabase CLI 3.1.0 com Auth, PostgreSQL 17 e Mailpit. Criou conta confirmada, repetiu o bootstrap, validou restrição de MFA, enviou e aceitou convite, recusou acesso cruzado, associou a mesma conta a outra empresa e recuperou a senha por e-mail local. |
| Migrações | `0001_platform` e `0002_invitation_delivery` aplicadas no Supabase local. Upgrade e downgrade foram ensaiados no PostgreSQL descartável com migrador sem superusuário. |
| `python -m ruff check backend scripts`, `npm ci`, `npm run build` e `node docs/fixtures/landing-dom-smoke.cjs` | Lint, compilação JavaScript e regressão da lógica da landing passaram. `dist/` contém os arquivos públicos de uma lista explícita. |
| `python scripts/local.py docker-up` e smoke HTTP em `127.0.0.1:8088` | Imagem Python 3.12 construída e contêiner saudável. Respostas 200 nas rotas públicas, no portal, no painel, em OpenAPI e nos healthchecks. A API privada retornou 401 sem sessão e a configuração local retornou 404. |
| `actionlint`, `docker compose … config --quiet` e `caddy adapt --config /etc/caddy/Caddyfile` | Workflow, configuração de Compose para proxy e sintaxe do Caddy validados com configuração sintética. |
| Inspeção visual | O navegador integrado não recebeu autorização para abrir a origem local. A inspeção visual e a navegação interativa no navegador ficam pendentes. |

Os comandos Python usam a `.venv` instalada pelo [README](../README.md). Sem variáveis de teste, `pytest -q backend` executa os testes independentes de serviços e marca as integrações locais como ignoradas.

Para reproduzir o teste SQL, inicialize um PostgreSQL descartável. O teste cria uma tabela sintética `auth.users` no banco de teste e aplica as migrações com a mesma política usada no runtime. As senhas abaixo são exemplos exclusivos do contêiner local. Mantenha a porta restrita a loopback.

```powershell
docker run --rm -d --name futuranext-pg-ci -e POSTGRES_DB=futuranext_test -e POSTGRES_PASSWORD=owner_sintetico_teste -p 127.0.0.1:55432:5432 postgres:17-alpine
docker exec futuranext-pg-ci pg_isready -U postgres -d futuranext_test
$env:TEST_MIGRATION_DATABASE_URL='postgresql+psycopg://postgres:owner_sintetico_teste@127.0.0.1:55432/futuranext_test'
$env:TEST_DATABASE_URL='postgresql+psycopg://fn_app:runtime_sintetico_teste_32@127.0.0.1:55432/futuranext_test'
python -m pytest backend/tests/test_postgres.py -q --tb=short
docker stop futuranext-pg-ci
```

Para reproduzir o fluxo com GoTrue e e-mail real de teste, use uma instalação Supabase CLI **local e descartável**, sem super admin persistente. O teste usa as portas locais da configuração do projeto e limpa as contas e empresas sintéticas que cria.

```powershell
python scripts/local.py setup
$env:FN_RUN_LOCAL_AUTH='1'
python -m pytest backend/tests/test_local_auth_flow.py -q --tb=short
Remove-Item Env:FN_RUN_LOCAL_AUTH
```

O modo local valida sessões consultando o Auth. A validação por JWKS usada no projeto hospedado foi exercitada isoladamente com assinatura assimétrica e chaves efêmeras. A entrega via Zoho, homologação em projeto Supabase hospedado e implantação externa permanecem pendentes de configuração e validação em ambiente autorizado.

## Matriz de cobertura da Fase 1

Os itens abaixo orientam verificações de regressão e a validação de cada ambiente.

| Camada | Cenários a demonstrar |
| --- | --- |
| API e contratos | OpenAPI coerente com as rotas; liveness e readiness com banco presente, ausente e schema incompatível; erro de entrada; origens permitidas e recusadas no CORS. |
| JWT | Aceitar sessão válida; rejeitar assinatura, algoritmo, emissor, audiência, expiração ou `sub` inválidos. Conferir rotação de JWKS e comportamento quando a verificação estiver indisponível. |
| Auth completo | Fluxo com Supabase local e Mailpit para login, convite, ativação, recuperação, reenvio, expiração, TOTP e logout. Verificar que e-mail confirmado errado não aceita convite e que uma conta pode integrar mais de uma empresa. |
| RBAC e licenciamento | Conta A lista apenas produtos com licença e ação permitida na empresa selecionada. Cobrir licença vencida, inativa ou ausente, empresa suspensa e permissão negada. Suporte só opera empresas atribuídas. Operações administrativas exigem MFA. |
| Isolamento pela API | Duas empresas sintéticas com IDs e dados distintos. Leitura, atualização e concessão de licença em empresa não autorizada devem falhar. Troca deliberada de contexto por conta multiempresa só retorna dados da empresa selecionada. Testar alterações de escopo e tentativas de delegação acima do papel do ator. |
| PostgreSQL e RLS | Rodar com PostgreSQL real e role de runtime. Sem contexto, acesso é negado. Validar leitura, escrita e atualização por tenant, políticas `WITH CHECK`, integridade de vínculos, privilégios diretos e limpeza após `COMMIT`/`ROLLBACK` e reuso da conexão. Exercitar vínculo cruzado e divergência entre tenant do contexto e o da linha. |
| Migração e bootstrap | Aplicar migrações em banco limpo e ver o efeito do upgrade em banco sintético. Testar compatibilidade com reversão documentada. Validar bootstrap repetido ou concorrente, UUID incorreto, auditoria e recusa de sessão sem `aal2`. |
| Frontend e publicação | Repetir regressão da landing, verificar rotas acessadas diretamente em local e Pages, navegar com teclado e tela pequena, alternar empresas sem estado residual e inspecionar a lista de arquivos de `dist/`. Conferir que saída de build e logs contêm apenas configuração pública. |

Os testes com retornos sintéticos cobrem falhas controladas e complementam os fluxos reais. Testes de Storage, jobs, anexos, webhooks e exportações entram com esses módulos.
