# Decisões e plano da Fase 1

Registro da Fase 0 em 14/11/2026. Os caminhos da tabela são planejados e poderão ser ajustados conforme a implementação e seus testes.

## Decisões arquiteturais

| Decisão | Justificativa e consequência |
| --- | --- |
| Preservar a landing e criar a área autenticada em vanilla JS | O HTML/CSS/JS existente atende à página pública. O portal modulariza cliente HTTP, Auth, navegação e formulários. Bundling restrito às dependências do portal reduz a superfície publicada e permite CSP sem CDN de scripts. |
| Usar FastAPI com SQLAlchemy, psycopg e Alembic | Rotas tipadas geram OpenAPI. Transações, DDL versionado e testes SQL exercitam os mesmos limites usados em operação. Separar funções de migrador, aplicativo e bootstrap. |
| Usar Supabase Auth e PostgreSQL | Reduz operação de identidade e banco. Adotar JWT assimétrico nos projetos hospedados e validar via JWKS confiável. A configuração local pode usar o endpoint de verificação de usuário do Supabase para projeto com chave legada, em modo explícito. Autorizações e licenças mantidas no banco podem mudar durante a validade do JWT. [Chaves de assinatura](https://supabase.com/docs/guides/auth/signing-keys) |
| Usar autorização da API e RLS com contexto transacional | A API verifica a ação solicitada. O PostgreSQL restringe as linhas e escritas disponíveis à sessão de runtime. O modelo exige consultas parametrizadas, roles separadas e testes diretos com a role da API. [RLS no PostgreSQL](https://www.postgresql.org/docs/current/ddl-rowsecurity.html) |
| Preferir a Opção A para clientes reais | Supabase Pro, Droplet de 2 GiB como dimensionamento inicial a medir, Caddy e Pages. Uma instância tem ponto único de falha. O piloto com Supabase Free aceita risco de pausa e recuperação manual. [Comparação de custos](CUSTOS.md) |
| Adotar fila PostgreSQL quando entrar processamento assíncrono | Usar a mesma transação para mudanças de negócio e enfileiramento. Manter worker separado do processo HTTP. Escolher recursos adicionais com dados de carga e requisitos de isolamento. |
| Separar catálogo, licença e operação de produto | Alterações no catálogo são administrativas. A disponibilidade de cada módulo acompanha seu desenvolvimento. Nenhuma licença autoriza automaticamente todas as ações. |

## Etapas de implementação

| Etapa | Arquivos previstos | Entrega e risco a verificar |
| --- | --- | --- |
| 1. Estrutura executável | `backend/pyproject.toml`, `backend/app/`, `Dockerfile`, `compose.yaml`, `.env.example`, `.gitignore`, `scripts/` | API inicial, healthchecks, Compose local e geração do frontend estático. Conferir portas, versões, checagens de configuração e lista pública. |
| 2. Banco, papéis e licenças | `backend/alembic.ini`, `backend/alembic/`, `backend/app/db/`, `backend/app/policies/`, `backend/tests/` | Migrações do núcleo, roles, permissões, licenças e RLS. Rodar com PostgreSQL real e verificar adoção/reversão e reuso de conexões. |
| 3. Auth e administração | `backend/app/auth/`, `backend/app/admin/`, `frontend/`, `supabase/config.toml`, `supabase/templates/`, `scripts/` | Login, convite, recuperação, MFA e bootstrap. CRUD mínimo de clientes e catálogo, gestão de usuários e licenças. Verificar idempotência e consistência quando Auth ou DB responderem com falha. |
| 4. Portal e navegação | `frontend/`, entrada de acesso em `index.html`, empacotamento em `scripts/` | Login acessível a partir de `/`, escolha de empresa, lista de produtos autorizados e perfil. Verificar navegação direta e regressão da landing, incluindo teclado, tela pequena e chat. |
| 5. Entrega local e CI | `README.md`, `backend/tests/`, `.github/workflows/`, configuração do proxy e `docs/` | Comandos de setup, testes, lint, verificação de segredos e build em GitHub Actions. Smoke test em Compose com Auth local, e-mails capturados e dados sintéticos. Registrar resultados e bloqueios com comandos exatos. |

## Controle de mudanças

O workspace recebido contém os arquivos extraídos e veio sem `.git`. O commit local `d4dc225` registra os sete arquivos recebidos. Os hashes em [Diagnóstico](DIAGNOSTICO_ATUAL.md) registram a referência para regressão. A documentação da Fase 0 fica em commit próprio. Para produção, usar branches e ambientes protegidos conforme o plano GitHub, build reproduzível e promoção manual após liberação explícita.

Antes de passar à Fase 2, confirmar a definição de pronto da Fase 1 pelos resultados de [Testes](TESTES.md). Convites, transições de licença, escalonamento de privilégios e isolamento de tenant requerem validação executada. A escolha de região da API e os fluxos fiscais e de mensagens dependem dos requisitos de cada cliente.
