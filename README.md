# FuturaNext

Plataforma multiempresa para comercializar e administrar ferramentas, usuários e serviços da FuturaNext. Reúne site institucional, portal do cliente, administração global, agenda, configurações fiscais, integrações externas e cobranças.

Este README concentra a passagem de contexto para outro desenvolvedor ou assistente de IA. Revisão de **17/11/2026**, baseada no código da `main` em **13e5d69**, nas migrações e em consultas de leitura ao ambiente instalado. Confira versões e configurações externas novamente ao iniciar uma manutenção.

## 1. Como continuar o desenvolvimento

1. Leia este documento e confira `git status -sb`, `git log -5 --oneline` e a tarefa atual do responsável.
2. Consulte o código, as migrações e os testes da área que será alterada. Os documentos em `docs/` também contêm propostas e estados de fases anteriores.
3. Preserve o isolamento entre empresas, a autorização na API e no PostgreSQL e os contratos dos módulos existentes.
4. Edite as fontes em `frontend/`, `backend/` e nos scripts. `dist/` é resultado de compilação.
5. Execute as verificações correspondentes à alteração. Informe o que foi verificado, publicado e o que continua pendente.
6. Use o fluxo de publicação da tarefa. O frontend usa GitHub e Cloudflare Pages. A API tem atualização própria no Droplet.

A solicitação atual do usuário define o escopo. Mudanças rotineiras e reversíveis podem seguir o contexto já autorizado, sem criar etapas de aprovação adicionais.

### Estado conhecido nesta revisão

| Componente | Estado verificado |
| --- | --- |
| Repositório | `https://github.com/futuranext/futuranext_portal`, branch `main`, commit `13e5d69` |
| Site | `https://futuranext.com.br`, Cloudflare Pages conectado ao GitHub |
| API | `https://api.futuranext.com.br`, imagem `futuranext-api:13e5d69`, publicada pelo [passo a passo](docs/ATUALIZAR_API.md) |
| Banco | Supabase PostgreSQL, revisão `0014_commercial` |
| Saúde da API | `/health/ready` respondeu com sucesso |
| Autenticação | Supabase Auth, MFA TOTP obrigatório e troca de senha inicial implementados |
| Exclusão de usuários | Implementada no portal e na API |
| E-mail inicial | Código publicado e variáveis Zoho presentes dentro do contêiner. Esta revisão conferiu configuração, sem enviar uma mensagem real |
| Asaas | Criação automática do cliente (`cus_…`) com notificações pagas desativadas. Chave da API e token de webhook configurados no runtime em 17/11/2026. Conferir o webhook no painel Asaas e homologar o checkout |
| Integrações externas | Contratos implementados. `FN_EXTERNAL_INTEGRATIONS` pendente no runtime consultado |
| Certificados fiscais | Chave mestra presente. Operação fiscal real depende de executor e homologação |
| Homologação | Topologia proposta nos documentos. Esta revisão confirmou o ambiente de produção |

Segredos, senhas de usuários, tokens OAuth e certificados permanecem na configuração privada do ambiente.

## 2. Arquitetura e mapa do código

| Parte | Implementação |
| --- | --- |
| Site público | HTML, CSS e JavaScript na raiz, identidade visual existente |
| Frontend autenticado | JavaScript modular, Supabase JS e esbuild |
| Backend | Python 3.12, FastAPI, Pydantic, SQLAlchemy e psycopg |
| Banco e identidade | PostgreSQL 17, Supabase Auth e Alembic |
| Frontend hospedado | Cloudflare Pages |
| API hospedada | Docker Compose em Droplet DigitalOcean, Caddy como proxy HTTPS |
| Desenvolvimento | Supabase CLI local, Docker e Mailpit |
| Primeiro acesso por e-mail | API HTTPS do Zoho Mail |
| Cobrança | API Asaas com checkout hospedado |

O navegador acessa o Pages, autentica no Supabase e envia o token Bearer à API. A API valida a identidade e executa SQL com contexto transacional e RLS. O backend é modular e o frontend carrega ferramentas sob demanda.

| Caminho | Responsabilidade |
| --- | --- |
| `index.html`, `styles.css`, `script.js`, `config.js`, `assets/` | Landing e contatos comerciais |
| `frontend/auth.html`, `frontend/workspace.html` | Modelos de páginas autenticadas |
| `frontend/js/client.js` | Sessão, SDK Auth e cliente HTTP |
| `frontend/js/auth.js`, `mfa.js`, `first-password.js`, `workspace.js` | Login, recuperação, MFA, senha inicial e inicialização |
| `frontend/js/admin.js`, `members.js`, `remove-user.js`, `user-access.js` | Administração, cadastro, exclusão e vínculos |
| `frontend/js/billing-admin.js`, `billing-portal.js` | Cobranças |
| `frontend/js/tools/index.js` | Registro e importação das ferramentas |
| `frontend/js/tools/` | Telas fiscais, agenda e integrações |
| `backend/app/main.py` | Ciclo de vida, routers, CORS, limites de corpo e páginas locais |
| `backend/app/auth.py`, `dependencies.py` | JWT, Supabase Admin e autorização da sessão |
| `backend/app/routes.py`, `schemas.py`, `repository.py` | Contratos e consultas do núcleo |
| `backend/app/database.py` | Contexto transacional e readiness |
| `backend/app/db_admin.py` | Papel de banco, primeiro administrador e suporte |
| `backend/app/first_access_email.py` | OAuth Zoho e e-mail inicial |
| `backend/app/modules/` | Agenda, fiscal, catálogo, WhatsApp e Asaas |
| `backend/alembic/versions/` | Histórico versionado do banco |
| `backend/tests/` | Testes de unidade, API, SQL e Auth local |
| `scripts/local.py` | Preparação e operação local |
| `scripts/build_frontend.mjs` | Build e lista de arquivos públicos |
| `supabase/config.toml`, `supabase/templates/` | Auth local e modelos de e-mail |
| `compose.yaml`, `Dockerfile`, `infra/` | API e infraestrutura |
| `.github/workflows/ci.yaml` | Verificações de CI |
| `.env.example` | Inventário de configuração |

### Rotas de interface

| Grupo | Rotas |
| --- | --- |
| Site | `/` |
| Autenticação | `/login`, `/recuperar-senha`, `/ativar-conta` |
| Portal | `/portal`, `/portal/produtos`, `/portal/perfil`, `/portal/usuarios`, `/portal/cobrancas` |
| Administração | `/admin`, `/admin/clientes`, `/admin/usuarios`, `/admin/produtos`, `/admin/assinaturas`, `/admin/acessos`, `/admin/solicitacoes`, `/admin/cobrancas` |
| Ferramenta | `/portal/produtos?ferramenta=<slug>` |
| Saúde | `/health/live`, `/health/ready` |
| Documentação local | `/docs` e `/openapi.json`, conforme `FN_OPENAPI_ENABLED` |

Novas páginas entram no gerador de frontend e, para atendimento pelo backend local, no mapa de páginas de `main.py`.

### Site institucional e atendimento comercial

- `config.js` configura os contatos públicos. WhatsApp usa DDI, DDD e número em dígitos, e `email` recebe a caixa comercial.
- Os CTAs abrem o roteiro de diagnóstico, mantido na memória da aba. Ao recusar a autorização de contato, o roteiro encerra a coleta.
- O visitante pode copiar ou baixar o resumo. Ações de WhatsApp e e-mail usam os contatos configurados e deixam o envio sob controle do visitante.
- Com contatos vazios, o roteiro e a exportação continuam disponíveis.
- Os nove cases e três depoimentos da versão inicial são demonstrativos.
- A calculadora, filtros, chat, autorização e exportação têm verificações em `docs/fixtures/landing-dom-smoke.cjs`.
- A landing pode ser vista por `python -m http.server 5500` na raiz. O login e as páginas autenticadas usam o ambiente completo descrito adiante.

## 3. Regras do núcleo multiempresa

### Empresas, perfis e acessos

- Empresa é o cliente contratante e corresponde a um `organization` ou `tenant`.
- Uma pessoa pode acessar várias empresas com a mesma conta. Cada vínculo possui perfil e estado ativo próprios.
- A empresa tem UUID, nome, slug único e estado ativo.
- A API recebe o UUID na rota e revalida a autorização a cada operação.
- Empresa, vínculo, produto e licença precisam atender às condições de acesso vigentes.
- O administrador global precisa também de vínculo de cliente para operar uma ferramenta no portal.
- Trocar de empresa na interface cancela a consulta anterior e limpa o conteúdo antes de carregar o novo contexto.
- Nas telas **Usuários**, **Licenças** e **Cobranças**, selecionar a empresa abre os formulários. O seletor pode iniciar vazio quando há várias empresas.

| Perfil | Regras |
| --- | --- |
| `super_admin` | Administração global de empresas, usuários, perfis de cliente, catálogo, licenças, acessos, solicitações e cobranças |
| `suporte` | Acesso administrativo de leitura limitado às atribuições ativas de suporte e às empresas elegíveis |
| `cliente_admin` | Administração dos usuários comuns e escrita nas ferramentas da própria empresa |
| `cliente_usuario` | Acesso às ferramentas permitidas da empresa, respeitando as permissões do produto |

O índice atual permite um `super_admin`. O bootstrap é idempotente para essa identidade. Somente o administrador global concede `cliente_admin`. Um `cliente_admin` gerencia `cliente_usuario` na própria empresa.

### Catálogo, ofertas e licenças

- Produto tem slug estável, nome, descrição, estado ativo, opção de oferta e URL HTTPS opcional.
- Licença vincula empresa e produto, com ativação, início e término opcional.
- Visibilidade exige produto ativo, licença vigente, empresa e vínculo ativos e permissão `product.view`, considerando overrides.
- Overrides por usuário existem no modelo e no SQL. A interface de **Acessos por usuário** administra vínculo e perfil por empresa.
- Escrita nas ferramentas exige `cliente_admin` na empresa.
- Produto ofertado e elegível aparece para solicitação no portal. Aprovação administrativa cria ou reativa a licença.
- Mensalidades ficam nos serviços de cobrança da empresa.
- Concessão de licença, aprovação de solicitação e pagamento possuem operações próprias.

### Clientes iniciais

Os registros e licenças iniciais são criados pela migração `0003_tools`.

| Nome no cadastro inicial | Slug da empresa | Ferramenta inicial |
| --- | --- | --- |
| Equinos Seguros | `equinos-seguros` | `cotacao-seguros` |
| CCP - Clegio Clemente Pavan | `ccp` | `nfse` |
| PetCamp | `petcamp` | `nfse-consulta` |
| Estetica Paraiso | `estetica-paraiso` | `clinica` |
| Barbearia Stylus | `barbearia-stylus` | `barbearia` |

O nome histórico da CCP foi mantido conforme o cadastro solicitado. O catálogo também inclui `whatsapp`. Um cliente pode contratar vários serviços, como hospedagem e WhatsApp.

## 4. Conta, senha, MFA e e-mails

### Autenticação e autorização

- Supabase Auth guarda senhas, sessões e fatores. A identidade usada é o UUID `sub`.
- Produção usa JWT com assinatura assimétrica e valida emissor, audiência, validade, papel e claims. O ambiente local permite validação remota contra o Auth local.
- Toda operação privada com `CurrentUser` exige troca de senha inicial concluída e `aal2`.
- O token é enviado como Bearer. A sessão do frontend fica em `sessionStorage` por aba.
- `tenant_repository` consulta `password_session_current` no banco para recusar conta excluída ou sessão de senha inicial substituída.
- A autorização também é aplicada no PostgreSQL.
- **Confiar no navegador por 15 dias continua pendente.** Essa evolução precisa definir persistência, prazo absoluto e revogação no servidor, preservando MFA para todas as contas.

### Convite pelo Supabase

1. Em **Administração → Usuários**, escolha a empresa e informe e-mail e perfil no formulário de convite.
2. O backend registra o convite e solicita o envio ao Supabase Auth.
3. O destinatário confirma o link, define a senha, configura MFA e aceita o vínculo.
4. Uma conta já confirmada pode receber acesso diretamente em **Acessos por usuário**.

O convite da aplicação tem validade padrão de sete dias. O registro pendente da mesma empresa/e-mail é reutilizado, com reenvio controlado em 60 segundos. O aceite repetido é idempotente e exige correspondência com o endereço confirmado no Auth.

A busca global inclui contas pendentes de confirmação para permitir exclusão. A concessão de vínculo pelo fluxo de acessos exige confirmação da conta.

### Cadastro por senha temporária

- Exclusivo do administrador global em **Administração → Usuários**.
- Senha com mínimo de 12 caracteres e máximo de 72 bytes UTF-8.
- Cria conta nova ou conclui pendência elegível da mesma empresa, sem vínculo e sem autenticador verificado.
- Confirma o endereço para login e marca a troca obrigatória. O administrador confere o destinatário antes de salvar.
- Conta ativa existente segue o fluxo de vínculos ou recuperação.
- Metadados do Auth usados são `futuranext_first_password`, `futuranext_password_company` e `futuranext_password_version`.
- A troca invalida a senha temporária e o acesso das sessões anteriores. O usuário entra novamente e conclui o MFA.

### E-mail de primeiro acesso

Depois do vínculo e do commit da transação, a API envia mensagem HTML pelo Zoho Mail com identidade FuturaNext, botão **Acessar o portal**, usuário e senha temporária. O botão abre `/login` na origem configurada.

| `email_delivery` | Significado no retorno do cadastro |
| --- | --- |
| `sent` | A API Zoho aceitou a mensagem |
| `unavailable` | Configuração do remetente incompleta |
| `failed` | Falha ou envio sem confirmação |

Uma falha mantém conta e vínculo e gera aviso no painel. A senha é limpa do formulário e fica fora dos logs, da resposta da API e de uma fila de reenvio. O fluxo atual faz uma tentativa. A confirmação na caixa do destinatário exige teste operacional.

Esse envio usa HTTPS/OAuth do Zoho. Convites e recuperação usam o provedor configurado no Supabase hospedado. Cada configuração é aplicada no respectivo serviço.

### Recuperação de senha

- Modelos versionados em `supabase/templates/recovery.html` e `invite.html`.
- O botão aponta à origem canônica com `token_hash` no fragmento e tipo do fluxo. O visitante confirma antes da validação.
- O frontend também atende callback PKCE. Seu verificador está na aba que iniciou o pedido. Outra aba, navegador ou origem pode causar a mensagem de link indisponível.
- Conta com TOTP exige o autenticador existente para a troca de senha pelo fluxo de recuperação.
- No Supabase hospedado, confira **Site URL**, redirecionamentos, SMTP, remetente e modelos.
- Os modelos hospedados são salvos separadamente no painel ou por Management API autorizada.
- Links têm uso único e expiração. Cada novo teste de recuperação começa por uma solicitação nova.

## 5. Exclusão permanente de usuários

Em **Administração → Usuários**, selecione a empresa, expanda o cadastro e abra **Excluir usuário**. Outra entrada é **Acessos por usuário**, pela busca de e-mail.

Contrato da API

~~~http
DELETE /api/v1/admin/users/{user_id}
Authorization: Bearer TOKEN
Content-Type: application/json

{"email":"usuario@example.com"}
~~~

- Exige administrador global, MFA e confirmação do e-mail correspondente ao UUID.
- Protege a própria conta e as contas internas de plataforma.
- Executa exclusão definitiva pelo Supabase Admin.
- O gatilho de `0010_user_deletion` limpa os convites na mesma transação. As FKs removem perfil, vínculos, permissões e auditoria pessoal.
- Auth remove sessões e fatores da identidade. A API recusa tokens antigos no próximo acesso protegido.
- O e-mail pode ser recadastrado com UUID novo e novos vínculos.
- Empresas, faturas e dados corporativos permanecem. Solicitações empresariais perdem a referência pessoal por `ON DELETE SET NULL`.
- Logs gerenciados pelo provedor e backups têm retenção própria.

Use uma conta de teste controlada para validar exclusão. A migração também afeta exclusões definitivas feitas diretamente no Supabase Auth.

## 6. Ferramentas e suas regras

### Agenda de estética e barbearia

Slugs `clinica` e `barbearia`. Base da API `/api/v1/tenants/{tenant_id}/tools/{tool_slug}/agenda`.

Implementa profissionais, serviços, produtos oferecidos, expediente, agendamentos, bloqueios, mensagens e métricas, com dados isolados por empresa e ferramenta.

- Horário civil em `America/Sao_Paulo`, persistindo instantes em UTC.
- Snapshot de um a 31 dias.
- Serviço com duração de cinco a 480 minutos e preço em centavos.
- Reserva guarda duração e cópia do preço do serviço.
- Atendimento inteiro dentro de expediente ativo, respeitando antecedência e horizonte de reserva.
- Banco e locks impedem sobreposição de reservas e bloqueios por profissional.
- Bloqueios manuais duram de um minuto a 31 dias e podem ocupar períodos fora do expediente.
- `request_id` e digest permitem repetir uma criação idêntica e recusam reutilização conflitante.
- Estados seguem `scheduled → confirmed/cancelled/completed/no_show` e `confirmed → cancelled/completed/no_show`.
- Conclusão e falta exigem início do atendimento. Estados finais encerram o fluxo.
- Remarcação preserva duração original e retorna a `scheduled`.
- Mudança de expediente recusa invalidar atendimentos futuros.
- Configuração guarda saudação, confirmação, cancelamento, encaminhamento, FAQ e prazos.
- `cancellation_notice_minutes` é armazenado. O cancelamento manual ainda precisa incorporar esse prazo, caso a regra seja exigida nesse fluxo.

O painel manual funciona. O chatbot conversacional e sua ligação com o WhatsApp continuam pendentes.

### CCP e PetCamp

Slugs `nfse` e `nfse-consulta`. Base `/api/v1/tenants/{tenant_id}/tools/{tool_slug}/fiscal`.

| Módulo | Funcionalidade |
| --- | --- |
| `nfse` | Prestador, ambiente, CNPJ, inscrição municipal, IBGE, tributação nacional/municipal, NBS, série DPS, descrição e planilhas |
| `nfse-consulta` | Ambiente e até 100 CNPJs distintos com nome e ativação individual |
| Ambos | Certificado A1 e acompanhamento da validade |

O normalizador verifica CNPJ numérico ou alfanumérico com dígitos verificadores.

- PKCS#12 `.pfx` ou `.p12`, chave privada, até 128 KiB.
- Verifica senha, chaves, vigência e características do certificado. A homologação precisa conferir cadeia e poderes aceitos pelo destino.
- O upload recusa certificado vencido ou ainda sem vigência. Esses estados podem aparecer depois na consulta de certificados já gravados.
- Arquivo e senha cifrados por AES-GCM, com `FN_FISCAL_MASTER_KEY` de 32 bytes e associação a empresa/slug.
- Runtime consulta datas e possui permissões específicas para gravar/remover o conteúdo protegido.
- Status de ausência, futura vigência, vigente, vencimento em até 30 dias e vencido.
- A chave mestra deve ser preservada entre atualizações para permitir a leitura dos certificados pelo executor.

Download de emissão com `variant=generico` ou `variant=ccp`. O formato CCP é `Base_Emissao_Nota_Fiscal.xlsx`, aba `Base`, com cabeçalhos compatíveis com o robô legado. Contrato em [NFSE.md](docs/NFSE.md).

Continuam pendentes upload/processamento de lotes, fila fiscal, executor, assinatura/transmissão DPS, consulta real de notas e checkpoint NSU. O legado CCP precisa ser localizado e homologado para essa integração.

### Equinos Seguros

`cotacao-seguros` abre o portal HTTPS dedicado no caminho `/portal`. Endereço associado ao UUID da empresa em `FN_EXTERNAL_INTEGRATIONS`.

Origem informada `C:\RPA\RPA_Cotacao_de_Seguro_V1.0`. O bot roda em máquina local com um clone. A integração oferece acesso ao frontend externo com autenticação e execução próprias, em endereço acessível aos usuários autorizados.

### WhatsApp

O serviço veio de `C:\Projetos\Api_WhatsApp` e agora fica em [`whatsapp/`](whatsapp/), sem o painel Next.js próprio. Tudo é feito no portal, em **Ferramentas › WhatsApp**: aceite, QR Code, webhook do bot e chaves de API. Publicação e uso pelo bot em [WHATSAPP_PUBLICACAO.md](docs/WHATSAPP_PUBLICACAO.md).

- **Aceite (`0012_whatsapp_consent`).** O administrador da empresa (`cliente_admin`) que vai parear o número recebe um e-mail com o link "Li e aceito". O link leva a `/portal/produtos?ferramenta=whatsapp&empresa=<id>#aceite=<token>`, só vale na sessão do mesmo usuário e expira em 24 horas. O banco guarda apenas o hash do token. Sem aceite, a API recusa conectar, mostrar o QR Code, cadastrar webhook e criar chaves.
- **Sem consentimento por destinatário.** O envio é bloqueado apenas para quem fez opt-out, respondendo SAIR, PARAR, STOP ou CANCELAR.
- **Serviço interno.** A API do portal chama `FN_WHATSAPP_SERVICE_URL` (`http://whatsapp-api:8000`) com `FN_WHATSAPP_SERVICE_TOKEN`, nas rotas `/service/v1/tenants/{tenant}/whatsapp`. O tenant do serviço é o UUID da empresa no portal. Há uma instância por empresa, criada automaticamente na primeira conexão.
- **Dados do serviço (`0013_whatsapp_service`).** O serviço usa o mesmo PostgreSQL, no schema privado `whatsapp`, com os papéis `wa_api` (backend) e `wa_worker` (worker). As tabelas têm RLS forçada, os grants são mínimos e o schema não aparece na Data API. Não há mais MongoDB. Licença (`tenant_products`) e aceite (`whatsapp_consents`) são conferidos no banco. A Evolution continua, temporariamente, com PostgreSQL próprio. Detalhes, mapa dos dados e validação em [WHATSAPP_POSTGRES.md](docs/WHATSAPP_POSTGRES.md).
- **Webhook do bot.** O cliente cadastra a URL HTTPS pública. O segredo `whsec_...` aparece uma única vez e fica criptografado em `whatsapp.callback_destinations`. O worker recarrega os destinos a cada 10 segundos. Assinatura HMAC-SHA256 em `X-Timestamp`/`X-Signature`, com eventos `message.received`, `message.status` e `instance.connection`.
- **Chaves `wp_...`.** Escopos `messages:send`, `messages:read` e `instances:read`, limitadas à instância da empresa, com validade de 365 dias e no máximo 10 ativas. São usadas em `FN_WHATSAPP_PUBLIC_API_URL` (`https://api.futuranext.com.br/whatsapp`). O Caddy publica só `/whatsapp/api/v1/messages*` e `/whatsapp/api/v1/instances*`.
- **Limites.** 1 envio a cada 5 segundos por número e 100 por dia por empresa.

O recebimento antigo de metadados (`/api/v1/integrations/whatsapp/{tenant_id}/events`, `FN_EXTERNAL_INTEGRATIONS.whatsapp_*`) continua no código, mas o portal não o usa mais. Veja [INTEGRACOES_EXTERNAS.md](docs/INTEGRACOES_EXTERNAS.md).

### Adicionar uma ferramenta

Por link, cadastre produto, URL HTTPS e licenças no painel. O destino aplica sua autenticação.

Para telas e dados próprios

1. Defina slug e contrato.
2. Crie módulo em `backend/app/modules/`, reutilizando `tenant_repository` e `require_tool`.
3. Acrescente migração com RLS e vínculos que preservem o tenant.
4. Registre router em `main.py`.
5. Implemente interface e importação em `frontend/js/tools/index.js`.
6. Cadastre produto/licenças e confira leitura, escrita e isolamento entre empresas.

## 7. Mensalidades e Asaas

### Operação

1. Cadastre a empresa com **CPF/CNPJ, e-mail de cobrança e telefone** (obrigatórios em **Administração → Empresas**).
2. Ao liberar o primeiro usuário, o cliente Asaas (`cus_…`) é criado e vinculado automaticamente. Em **Administração → Cobranças**, o painel **Cliente Asaas** mostra o estado e oferece **Criar cliente no Asaas agora** ou o vínculo manual de um cliente existente.
3. Em **Serviços mensais**, cadastre ou abra o serviço e edite **Valor mensal em reais**.
4. Gere fatura com competência e vencimento.
5. O cliente paga em **Portal → Pagamento** (`/portal/pagamento`; `/portal/cobrancas` continua abrindo a mesma tela), escolhendo boleto, Pix ou cartão de crédito sem sair do portal.

### Regras

- Cliente Asaas criado automaticamente (`0011_billing_customer`) quando a empresa está ativa, tem CPF/CNPJ, e-mail e telefone e possui ao menos um usuário com acesso. Disparos: abertura de Cobranças no admin ou no portal, cadastro de usuário por senha temporária, edição da empresa e emissão de fatura.
- A criação envia `name`, `cpfCnpj`, `email`, `mobilePhone` (11 dígitos) ou `phone`, `externalReference=futuranext:tenant:<uuid>` e **`notificationDisabled=true`**. A opção de notificação por e-mail/SMS do Asaas tem custo extra e nunca é ativada. Vínculos manuais também têm as notificações desativadas.
- O Asaas exige `cpfCnpj` na API de clientes, por isso o documento é obrigatório além de nome, e-mail e telefone.
- A busca por `externalReference` antes do POST e um lock por empresa evitam clientes duplicados. Editar a empresa atualiza o cliente já vinculado.
- Um usuário do cliente só grava o primeiro vínculo automático (flag `fn.billing_customer_sync` na transação). Troca de vínculo continua administrativa.
- Cobrança por empresa, com vários serviços e valores independentes.
- Vínculo Asaas por ambiente e cliente. Um cliente Asaas de um ambiente pertence a uma empresa.
- Valores em centavos, de R$ 0,01 a R$ 100.000 por serviço.
- Emissão manual por competência, com um a 100 serviços ativos.
- Fatura soma serviços ativos e guarda itens/preços da emissão.
- Alterar preço afeta próximas emissões.
- Unicidade por empresa/competência considera cancelamento e existência de cobrança externa.
- Vencimento atual ou futuro no fuso de São Paulo.
- A fatura local precede o checkout. Iniciar pagamento cria cobrança Asaas com `billingType=UNDEFINED`.
- A mesma cobrança `UNDEFINED` atende os três meios, sem trocar `billingType`:
  - **Boleto** `POST .../payment/boleto` devolve a linha digitável (`/identificationField`) e o endereço do PDF (`bankSlipUrl`, aceito só em `/b/pdf/` do host Asaas do ambiente). `GET .../payment/boleto.pdf` baixa o PDF pela API, com limite de 3 MB. O portal oferece copiar o código, abrir o boleto e baixar o PDF.
  - **Pix** `POST .../payment/pix` devolve o QR Code (`/pixQrCode`, `encodedImage`) e o copia e cola. Em produção, mantenha uma chave Pix cadastrada no Asaas. Sem ela, o QR dinâmico vale só até 23h59 do dia.
  - **Cartão** `POST .../payment/card` recebe os dados do cartão e do titular (nome, CPF/CNPJ, e-mail, telefone, CEP e número) e chama `/payWithCreditCard`. O número passa pelo algoritmo de Luhn e a validade é conferida. Os dados não são gravados nem registrados em log. A fatura fica travada durante a chamada e o estado é consultado antes, o que impede pagar duas vezes. Recusa vira `422` com a mensagem do Asaas. Resposta incerta deixa a fatura `processing`, sem repetir a cobrança, até a consulta ou o webhook. Desafio 3DS devolve `challenge_url`.
  - Como os dados do cartão passam pela API, o ambiente entra no escopo PCI-DSS. HTTPS é obrigatório. Se o Asaas habilitar a tokenização na conta, considere migrar.
- Referência `futuranext:invoice:<uuid>`.
- Estados do provedor `unsubmitted`, `inflight`, `uncertain`, `linked`, `rejected`.
- Resposta incerta exige conciliação por referência antes de outra cobrança.
- Cancelamento administrativo atende fatura local elegível anterior ao encaminhamento ao Asaas.
- Webhook `/api/v1/integrations/asaas/events`, com `asaas-access-token`, deduplicação e consulta do estado atual no provedor.
- Conciliação confere cliente, referência, valor e vencimento.
- Recorrência automática e mudança automática de licença por pagamento continuam pendentes.

Estados exibidos incluem `open`, `paid`, `confirmed`, `overdue`, `processing`, `refunded`, `cancelled` e `review`.

Antes de ativar pagamentos reais, configure credenciais e homologue o formato atual da URL devolvida pelo Asaas. O adaptador valida caminho `/i/<id>`, que precisa acompanhar o contrato do provedor. Confira as assinaturas existentes do projeto WhatsApp ao consolidar serviços.

## 8. Banco, segurança e migrações

- `auth` contém identidade gerenciada.
- `futuranext` contém dados da aplicação.
- `futuranext_meta.alembic_version` registra a migração.
- `fn_app` é runtime com privilégios limitados e RLS.
- A conexão de migração tem DDL e uso operacional.
- A transação aplica `fn.actor_id`, `fn.tenant_id` e `fn.aal` com `set_config(..., true)`.
- Tabelas de domínio mantêm associação com tenant, inclusive entre pais e filhos.
- Chave administrativa do Supabase é exclusiva do backend.
- API limita corpo, aplica CORS por origens exatas e omite valores sensíveis em erros de validação.
- Build seleciona explicitamente os arquivos públicos.
- Auditoria registra alterações. Exclusão pessoal segue as regras da seção de usuários.

| Revisão | Conteúdo |
| --- | --- |
| `0001_platform` | Núcleo, autorização e RLS |
| `0002_invitation_delivery` | Entrega e aceite de convites |
| `0003_tools` | Ferramentas, acessos, solicitações e clientes |
| `0004_fiscal` | Configurações e certificados |
| `0005_agenda` | Agenda e conversação configurável |
| `0006_integrations` | WhatsApp e eventos |
| `0007_agenda_products` | Produtos da agenda |
| `0008_first_password` | Senha inicial e sessões |
| `0009_billing` | Serviços e faturas |
| `0010_user_deletion` | Exclusão pessoal, convites e auditoria |
| `0011_billing_customer` | CPF/CNPJ, e-mail e telefone de cobrança da empresa e vínculo automático do cliente Asaas |
| `0012_whatsapp_consent` | Aceite por e-mail do administrador que conecta o WhatsApp |
| `0013_whatsapp_service` | Schema privado `whatsapp` do serviço (antes MongoDB), papéis `wa_api`/`wa_worker`, RLS e funções de licença/aceite |
| `0014_commercial` | Preço das ferramentas, compra pelo portal, assinatura mensal no Asaas (liga/desliga por empresa), faturas da assinatura via webhook e teste grátis de 15 dias |

Head atual `0014_commercial`. A readiness exige revisão exata, runtime `fn_app` e RLS ativo. Ao criar migração, ajuste `Database.ready()` e os testes dependentes da revisão.

Migrações publicadas compõem o histórico. Evoluções posteriores recebem revisão nova, aplicada pela imagem que a contém.

`0010` instala gatilho em `auth.users` e FK na auditoria. O migrador precisa de `TRIGGER` e `REFERENCES`, conferidos na instalação durante esta fase.

## 9. Configuração

Inventário em [.env.example](.env.example). Desenvolvimento usa `.env.local`. Servidor usa `.env.runtime` e `.env.migration`.

| Variáveis | Uso |
| --- | --- |
| `FN_ENVIRONMENT` | `local`, `test`, `hml` ou `production` |
| `FN_DATABASE_URL` | Runtime privado, papel `fn_app` |
| `FN_MIGRATION_DATABASE_URL` | Conexão administrativa |
| `FN_APP_DB_PASSWORD` | Provisionamento/rotação de `fn_app` |
| `FN_SUPABASE_URL`, `FN_SUPABASE_ISSUER` | URL e emissor terminado em `/auth/v1` |
| `FN_SUPABASE_PUBLISHABLE_KEY` | Chave publicável para Auth |
| `FN_SUPABASE_ADMIN_KEY` | Chave administrativa do backend |
| `FN_AUTH_MODE` | `remote` local, `jwks` hospedado |
| `FN_FRONTEND_URL`, `FN_CORS_ORIGINS` | Origem canônica e array JSON de origens permitidas |
| `FN_STATIC_DIR`, `FN_OPENAPI_ENABLED` | Arquivos locais e documentação |
| `FN_FISCAL_MASTER_KEY` | Chave de 32 bytes Base64 URL-safe |
| `FN_EXTERNAL_INTEGRATIONS` | JSON privado por UUID |
| `FN_ASAAS_ENVIRONMENT`, `FN_ASAAS_API_KEY`, `FN_ASAAS_WEBHOOK_TOKEN` | Ambiente, chave e webhook |
| `FN_ZOHO_MAIL_REGION` | `us`, `eu`, `in`, `au`, `jp`, `ca`, `cn`, `ae` ou `sa` |
| `FN_ZOHO_MAIL_CLIENT_ID`, `FN_ZOHO_MAIL_CLIENT_SECRET`, `FN_ZOHO_MAIL_REFRESH_TOKEN` | OAuth da caixa |
| `FN_ZOHO_MAIL_ACCOUNT_ID`, `FN_ZOHO_MAIL_SENDER` | Caixa e remetente autorizado |
| `FN_API_IMAGE` | Imagem executada pelo Compose |
| `FN_API_HOSTNAME`, `FN_CADDY_CERT_DIR` | Variáveis do exemplo de proxy conforme instalação |
| `PUBLIC_SUPABASE_URL`, `PUBLIC_SUPABASE_PUBLISHABLE_KEY`, `PUBLIC_API_BASE_URL` | Valores públicos embutidos no build |

Use aspas simples em valores com `$` nos arquivos do Compose, como algumas chaves Asaas, para evitar interpolação.

`PUBLIC_*` fica acessível no navegador. Chaves administrativas, OAuth, segredo Asaas e certificados pertencem ao runtime privado.

Zoho usa `ZohoMail.messages.CREATE`. A consulta opcional do ID da caixa usa `ZohoMail.accounts.READ`. Remetente esperado `suporte@futuranext.com.br`, caixa ou alias autorizado. Datacenter e credenciais correspondem à mesma conta. Procedimento em [INTEGRACOES.md](docs/INTEGRACOES.md#e-mail-de-primeiro-acesso-por-senha-temporária).

O Droplet usa HTTPS para Zoho. SMTP/modelos de convites e recuperação são configurados no Supabase hospedado.

## 10. Desenvolvimento e testes

### Instalação

Requer Python 3.12+, Node.js 22+, npm, Git e Docker/Compose. CI usa Python 3.12 e Node 24. Supabase CLI fixado em `3.1.0`. O primeiro setup baixa pacotes e imagens.

Na raiz pelo PowerShell

~~~powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e './backend[dev]'
.venv/Scripts/python.exe scripts/local.py setup
.venv/Scripts/python.exe scripts/local.py serve
~~~

Linux/macOS usam `.venv/bin/python`.

Setup cria rede `futuranext-local` com bind de loopback, inicia Supabase, prepara `fn_app`, migra e compila. Gera `.env.local` ignorado pelo Git e preserva a senha básica de runtime.

**O setup regrava as variáveis básicas de `.env.local`.** Preserve configurações extras privadas, como Zoho, chave fiscal e integrações, antes de repeti-lo. Para atualizações diárias, use `build`, `migrate` e `serve`.

| Acesso | Endereço |
| --- | --- |
| Aplicação e API | `http://127.0.0.1:8088` |
| Supabase API/Auth | `http://127.0.0.1:54321` |
| PostgreSQL | `127.0.0.1:54322` |
| Studio | `http://127.0.0.1:54323` |
| Mailpit do Auth | `http://127.0.0.1:54324` |
| Documentação | `http://127.0.0.1:8088/docs` |

Use `127.0.0.1` para manter a origem dos redirecionamentos. O primeiro e-mail Zoho usa seu adaptador próprio, simulado nos testes.

### Primeira conta e rotina

Crie uma conta de teste confirmada em Studio → Authentication → Users, copie UUID e execute bootstrap. Entre em `/login`, configure TOTP e abra `/admin`. Conceda vínculo de cliente para operar ferramentas.

~~~powershell
.venv/Scripts/python.exe scripts/local.py bootstrap-admin UUID
.venv/Scripts/python.exe scripts/local.py build
.venv/Scripts/python.exe scripts/local.py migrate
~~~

Para API em contêiner, encerre a API nativa antes de ocupar a mesma porta

~~~powershell
.venv/Scripts/python.exe scripts/local.py docker-up
.venv/Scripts/python.exe scripts/local.py docker-down
npx --yes supabase@3.1.0 stop
~~~

O último comando preserva dados locais. Em conflito de rede anterior do Supabase, pare a pilha e refaça setup.

Suporte usa CLI em `backend/` com conexão de migração no ambiente

~~~bash
python -m app.db_admin assign-support UUID_USUARIO UUID_EMPRESA
python -m app.db_admin revoke-support UUID_USUARIO UUID_EMPRESA
~~~

### Verificações

~~~powershell
.venv/Scripts/python.exe -m ruff check backend scripts
.venv/Scripts/python.exe -m pytest -q backend
node docs/fixtures/landing-dom-smoke.cjs
.venv/Scripts/python.exe scripts/local.py build
~~~

- Sem variáveis de integração, testes de PostgreSQL/Auth são ignorados.
- SQL requer `TEST_MIGRATION_DATABASE_URL` e `TEST_DATABASE_URL` em PostgreSQL descartável de loopback com nome contendo `test`. Credenciais dos exemplos de CI são sintéticas.
- Auth/Mailpit requer `FN_RUN_LOCAL_AUTH=1` e `backend/tests/test_local_auth_flow.py`.
- Esse teste usa Supabase descartável sem administrador persistente e limpa seus dados sintéticos.
- Zoho é simulado por `httpx.MockTransport`, cobrindo envio, falha, escaping, OAuth e dados protegidos.
- A última execução completa antes deste README teve **91 testes aprovados**, incluindo SQL, Auth local e Zoho simulado.

CI verifica histórico com Gitleaks, frontend, lint, pytest com PostgreSQL e imagem Docker. Pages publica o frontend automaticamente.

## 11. Publicação e operação

### Instalação conhecida

| Recurso | Identificação |
| --- | --- |
| GitHub | `futuranext/futuranext_portal` |
| Cloudflare Pages | `futuranext-portal`, produção pela `main` |
| Site | `https://futuranext.com.br` |
| API | `https://api.futuranext.com.br` |
| Supabase | Projeto `wcskiimwoqgauehwcwyo` |
| Droplet | `137.184.163.18`, Linux amd64 |
| Diretório | `/opt/futuranext/futuranext_portal` |
| Configuração privada | `.env.runtime` e `.env.migration`, modo 600 |
| Serviço | `futuranext-api-1`, Compose project `futuranext` |
| Proxy | `futuranext-proxy-1`, Caddy |
| Imagens | `futuranext-api:<sha-curto>` |
| Releases/backups | Diretórios privados `/opt/futuranext/deploy-*` |

Conexão instalada usa Session Pooler Supabase na porta 5432 e identidades separadas de migrador/runtime. Operações anteriores usaram `PGSSLMODE=require`. O TLS acompanha a conexão validada da instalação.

A chave SSH histórica está em `$env:USERPROFILE\.ssh\futuranext_digitalocean`. Outra pessoa precisa de chave pública autorizada ou console DigitalOcean. A chave privada e a senha ficam com o titular.

### Frontend

1. Commit e push das fontes na `main`.
2. Conferir CI no GitHub e commit do deployment de produção no Pages.
3. Testar o domínio canônico.

Build `npm ci && npm run build`, saída `dist/` e três variáveis `PUBLIC_*` do ambiente.

`npm run build` aceita configuração pública vazia para CI. Compilação e configuração do login publicado devem ser verificadas em seus contextos.

### API no Droplet

> Passo a passo detalhado para o console web do DigitalOcean, com resultados esperados, configuração de chaves e retomada: [docs/ATUALIZAR_API.md](docs/ATUALIZAR_API.md).

O push disponibiliza fontes. Imagem, Compose e migrações têm atualização própria.

O checkout instalado pode estar em commit antigo com mudanças de implantação. O procedimento usa `git fetch` e `git archive` de um SHA definido para uma pasta nova, preservando os arquivos privados atuais.

Use a mesma sessão SSH Bash para os blocos seguintes. Substitua `SHA_PUBLICADO` pelo SHA completo da release.

~~~bash
set -Eeuo pipefail
umask 077
cd /opt/futuranext/futuranext_portal
exec 9>/opt/futuranext/.portal-deploy.lock
flock -n 9

git fetch origin main
revision=SHA_PUBLICADO
git cat-file -e "$revision^{commit}"
short=$(git rev-parse --short=7 "$revision")
release=$(mktemp -d "/opt/futuranext/deploy-$short.XXXXXX")
mkdir "$release/source"
git archive "$revision" | tar -x -C "$release/source"

install -m 600 .env.runtime "$release/env.runtime.before"
install -m 600 compose.yaml "$release/compose.before"
docker inspect futuranext-api-1 --format '{{.Config.Image}}' > "$release/image.before"
image="futuranext-api:$short"
docker build --platform=linux/amd64 --tag "$image" "$release/source"

migrate() {
    docker run --rm -e PGSSLMODE=require -e PGCONNECT_TIMEOUT=10 \
      -e 'PGOPTIONS=-c lock_timeout=5s' --env-file .env.migration \
      "$image" python -m alembic "$@"
}
migrate current
migrate heads
~~~

Confira revisão atual e head antes de aplicar. No código inspecionado, head `0014_commercial` (publicação em [LANCAMENTO_COMERCIAL.md](docs/LANCAMENTO_COMERCIAL.md)). A 0013 cria os papéis `wa_api`/`wa_worker` sem login; ative-os com `python -m app.db_admin setup-whatsapp-roles` (variáveis `WA_API_DB_PASSWORD` e `WA_WORKER_DB_PASSWORD`).

### Backup antes da migração

A exportação privada cobre `auth`, `futuranext` e `futuranext_meta`. Inclui dados de autenticação e usa a conexão de migração instalada. Os arquivos ficam no diretório protegido da release.

~~~bash
python3 - "$release" <<'PY'
from pathlib import Path
from sys import argv
from urllib.parse import unquote, urlsplit

entry = next(line.split("=", 1)[1] for line in Path(".env.migration").read_text().splitlines()
             if line.startswith("FN_MIGRATION_DATABASE_URL="))
url = urlsplit(entry.strip().strip(chr(34)).strip(chr(39)))
fields = {
    "PGHOST": url.hostname or "",
    "PGPORT": str(url.port or 5432),
    "PGDATABASE": unquote(url.path.lstrip("/")),
    "PGUSER": unquote(url.username or ""),
    "PGPASSWORD": unquote(url.password or ""),
    "PGSSLMODE": "require",
    "PGCONNECT_TIMEOUT": "10",
}
if not (fields["PGHOST"].endswith(".pooler.supabase.com")
        and fields["PGPORT"] == "5432"
        and fields["PGUSER"] == "postgres.wcskiimwoqgauehwcwyo"
        and fields["PGDATABASE"] == "postgres" and fields["PGPASSWORD"]):
    raise SystemExit("Confira o destino de produção da migração.")
if any(any(ch in value for ch in "\r\n\0") for value in fields.values()):
    raise SystemExit("Configuração inválida.")
target = Path(argv[1]) / "backup.env"
target.write_text("".join(f"{key}={value}\n" for key, value in fields.items()))
target.chmod(0o600)
PY

trap 'rm -f -- "$release/backup.env"' EXIT
docker run --rm --network host --env-file "$release/backup.env" postgres:17-alpine \
  pg_dump --no-password --format=custom --lock-wait-timeout=10s --strict-names \
  --schema=auth --schema=futuranext --schema=futuranext_meta > "$release/pre-deploy.dump.part"

docker run --rm -i postgres:17-alpine pg_restore --list \
  < "$release/pre-deploy.dump.part" > "$release/pre-deploy.dump.toc"
grep -q ' TABLE DATA auth users ' "$release/pre-deploy.dump.toc"
docker run --rm -i postgres:17-alpine pg_restore --file=/dev/null \
  < "$release/pre-deploy.dump.part"
mv "$release/pre-deploy.dump.part" "$release/pre-deploy.dump"
(cd "$release" && sha256sum pre-deploy.dump > pre-deploy.dump.sha256 && sha256sum -c pre-deploy.dump.sha256)
rm -f -- "$release/backup.env"
trap - EXIT
~~~

Arquivos de Storage, configuração de fornecedores e certificados do proxy têm cópias próprias. Restauração considera o destino e as alterações posteriores ao backup.

### Aplicar e conferir

O bloco Python abaixo prepara `"$release/env.runtime.next"` com os valores atuais e a imagem nova. Se a release exigir variáveis adicionais, edite esse arquivo preparado depois do bloco Python e antes do `config --quiet`.

~~~bash
python3 - "$release" "$image" <<'PY'
from pathlib import Path
from sys import argv
import re

release, image = Path(argv[1]), argv[2]
source = Path(".env.runtime").read_text()
updated, count = re.subn(r"(?m)^FN_API_IMAGE=[^\n]*", f"FN_API_IMAGE={image}", source)
if count != 1:
    raise SystemExit("Confira FN_API_IMAGE no runtime.")
(release / "env.runtime.next").write_text(updated)
PY

docker compose -p futuranext --project-directory "$PWD" \
  --env-file "$release/env.runtime.next" -f "$release/source/compose.yaml" config --quiet

cmp -s .env.runtime "$release/env.runtime.before"
cmp -s compose.yaml "$release/compose.before"
migrate upgrade head
migrate current

install -m 600 "$release/env.runtime.next" .env.runtime.deploy-next
install -m 644 "$release/source/compose.yaml" compose.yaml.deploy-next
mv .env.runtime.deploy-next .env.runtime
mv compose.yaml.deploy-next compose.yaml
docker compose -p futuranext --env-file .env.runtime -f compose.yaml \
  up -d --no-deps --no-build --pull never --wait --wait-timeout 90 api

curl -fsS https://api.futuranext.com.br/health/live
curl -fsS https://api.futuranext.com.br/health/ready
docker ps --filter name=futuranext --format '{{.Names}} {{.Image}} {{.Status}}'
flock -u 9
~~~

O comando seleciona `api`. Caddy segue com seu serviço instalado. Preserve essa seleção ao atualizar a operação.

`setup-role` no servidor é usado em provisionamento/rotação coordenada de `fn_app`. Deploy comum usa Alembic na imagem nova.

Finalize com login, leitura autorizada e cenários da alteração. Rotas privadas sem token recusam acesso. Confira CORS de `https://futuranext.com.br`. Exclusão usa conta de teste. E-mail exige conferir mensagem e primeiro login de um destinatário controlado.

### Falha e reversão

- Guarde SHA, diretório da release, imagem anterior e backup.
- Falha de build/backup é corrigida antes de migrar.
- Entre migração e troca da API, a imagem anterior pode retornar readiness 503 por divergência de revisão.
- Reversão exige conferir compatibilidade da imagem com o schema e selecionar deployment Pages correspondente.
- Downgrade de `0009_billing` remove tabelas de cobrança. Dados e cobranças externas precisam de recuperação específica.
- Downgrade de `0010` remove seus mecanismos. Contas já apagadas permanecem apagadas.
- Preserve chave fiscal, credenciais e configuração do proxy.

Diretórios temporários de atendimentos anteriores pertencem a releases específicas. O procedimento acima usa o repositório e a configuração privada existente.

## 12. Pendências e diagnóstico

| Sintoma ou tarefa | Conferência ou próxima ação |
| --- | --- |
| Botão ausente | Commit no Pages, perfil, empresa selecionada e cadastro expandido. Recarregar com `Ctrl+F5` |
| Nova rota retorna 404 | Imagem executada e router. Pages pode ter terminado antes da atualização da API |
| Readiness 503 | Banco, runtime, RLS, revisão e imagem |
| Recuperação com erro | Modelo hospedado, `token_hash`, origem, link novo ou aba original do PKCE |
| Primeiro e-mail sem confirmação | `email_delivery`, OAuth, datacenter, caixa, remetente e variáveis do contêiner |
| E-mail aceito pelo Zoho | Validar recebimento, botão, credencial e troca inicial |
| Pagamento indisponível | Asaas, cliente do ambiente, serviços, webhook e homologação do checkout |
| WhatsApp "sendo preparada" | Variáveis `FN_WHATSAPP_*` e serviço `futuranext-whatsapp` ([guia](docs/WHATSAPP_PUBLICACAO.md)) |
| Equinos sem endereço | `FN_EXTERNAL_INTEGRATIONS` por empresa |
| Certificado indisponível | Chave, arquivo, MFA e perfil |
| MFA por 15 dias | Confiança, expiração e revogação |
| Robô fiscal | Executor e homologação do legado |
| Chatbot | Conversação e eventos WhatsApp ligados à agenda |
| Recorrência | Calendário, geração, reconciliação e regra comercial de licença |

Prioridades comerciais conhecidas são vender ferramentas modularmente, aceitar vários serviços por cliente e manter manutenção centralizada e rápida. Módulos e carregamento sob demanda já apoiam essa estrutura. Executores futuros de longa duração precisam tratar fila durável, idempotência e reconciliação.

### Projetos externos citados

| Origem histórica | Uso |
| --- | --- |
| `C:\Projetos\Api_WhatsApp` | Origem do serviço, hoje em `whatsapp/` |
| `C:\RPA\RPA_Cotacao_de_Seguro_V1.0` | Frontend e bot Equinos |
| `G:\Meu Drive\JCBuso\Clemente_Pavan\Emissor_NFse_Nacional.zip` | Legado fiscal CCP |

São caminhos da máquina do responsável. Em outro ambiente, solicite localização/acesso quando a tarefa depender desse material. O núcleo tem fluxo local independente dessas integrações.

### Documentação complementar

- [Arquitetura](docs/ARQUITETURA.md)
- [Operação das ferramentas](docs/PORTAL_FERRAMENTAS.md)
- [Integrações e Zoho](docs/INTEGRACOES.md)
- [WhatsApp e Equinos](docs/INTEGRACOES_EXTERNAS.md)
- [Fiscal](docs/NFSE.md)
- [Testes](docs/TESTES.md)
- [Segurança](docs/SEGURANCA.md)
- [Decisões](docs/DECISOES.md)
- [Custos e fornecedores](docs/CUSTOS.md)
- [Atualizar a API no Droplet](docs/ATUALIZAR_API.md)
- [Publicar e usar o WhatsApp](docs/WHATSAPP_PUBLICACAO.md)
- [Implantação histórica](docs/DEPLOY_DIGITALOCEAN.md) e [primeira publicação](docs/PRIMEIRA_PUBLICACAO.md)

Guias antigos mencionam `0009_billing`, entrega manual da senha, ZIP ou desativação de deploy automático. Este README registra a instalação inspecionada com `0010`, Zoho inicial e Pages por GitHub. Confira código e ambiente ao retomar uma tarefa.

### Contexto breve para outra IA

> Trabalhe no repositório FuturaNext e leia o README antes de alterar o código. A plataforma é multiempresa, com frontend HTML/JS, API FastAPI e Supabase Auth/PostgreSQL. Preserve RLS, permissões por empresa, MFA obrigatório e fluxos de senha/exclusão. Confira commit, revisão Alembic e estado do ambiente. Pages publica frontend pela main, e a API tem deploy próprio no Droplet. O README mapeia regras, fontes, configuração, testes, operação e pendências. Implemente a solicitação atual, reutilize contratos existentes e informe as verificações e publicações efetivamente concluídas.
