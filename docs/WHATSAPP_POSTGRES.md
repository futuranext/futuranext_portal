# Serviço WhatsApp no PostgreSQL do portal

O serviço WhatsApp (`whatsapp/`) deixou de usar MongoDB. Os dados dele agora ficam no **mesmo PostgreSQL (Supabase) do portal**, em um schema privado `whatsapp`, criado pela migração Alembic `0013_whatsapp_service`. Redis continua sendo usado só para QR Code temporário, limites de taxa e sinalização da fila.

> **Não há dados MongoDB para migrar em produção.** O serviço nunca foi publicado no Droplet: `https://api.futuranext.com.br/whatsapp/...` respondia 404 e não existiam a stack nem as variáveis `FN_WHATSAPP_*`, conferido em 18/11/2026. Se algum ambiente tiver dados no Mongo, eles precisam de um script de cópia próprio, que **não** está incluído aqui.

## 1. Mapa: coleção MongoDB → PostgreSQL

| Coleção Mongo | Destino | Observações |
| --- | --- | --- |
| `instances` | `whatsapp.instances` | `UNIQUE (tenant_id, name)`, `UNIQUE provider_ref`, FK `tenant_id → futuranext.organizations(id)` |
| `api_keys` | `whatsapp.api_keys` + `whatsapp.api_key_instances` | Só o HMAC do segredo. `instance_ids` virou tabela com FKs compostas (`tenant_id, key_id`) e (`tenant_id, instance_id`) |
| `messages` | `whatsapp.messages` | Idempotência: índice único parcial (`tenant_id, instance_id, idempotency_key`) para saída. Entrada: único (`tenant_id, instance_id, provider_message_id`). Telefone e texto cifrados (Fernet) |
| `suppressions` | `whatsapp.suppressions` | Opt-out do destinatário (SAIR/PARAR/STOP/CANCELAR) |
| `consents` | não migrada | O envio exige só a ausência de opt-out. `POST /consents` continua respondendo `410` |
| `gateway_events` | `whatsapp.gateway_events` | PK = HMAC do evento (deduplicação). FK composta para a instância |
| `callback_destinations` | `whatsapp.callback_destinations` | URL e segredo cifrado do webhook do bot |
| `webhook_deliveries` | `whatsapp.webhook_deliveries` | Fila de entregas. FK composta para o evento de origem (`ON DELETE CASCADE`) |
| `audit_events` | `whatsapp.audit_events` | Retenção de 180 dias |
| `schema_migrations` | Alembic (`futuranext_meta.alembic_version`) | As migrações Mongo (`migrations.py`, `*_migration.py`) foram removidas |
| `tenants` | **reutiliza** `futuranext.organizations` | O tenant é o UUID da empresa no portal |
| `users`, `sessions`, `account_requests` | **removidas** | Login próprio duplicado. O login é do portal |
| `billing_*` | **removidas** | Cobrança duplicada. A cobrança é do portal (Asaas) |
| `consent_requests` | **removida** | Aceite por destinatário via e-mail. Decisão: o aceite é do administrador que pareia |

### Estruturas do portal reutilizadas

| Tabela do portal | Uso pelo serviço |
| --- | --- |
| `futuranext.tenant_products` + `products` + `organizations` | Função `whatsapp.tenant_licensed(org)`. Chave de API, criação/conexão de instância, envio da fila e entrega de webhooks exigem licença vigente |
| `futuranext.whatsapp_consents` | Função `whatsapp.terms_accepted(org, user)`. `connect` e `qr` exigem o aceite do usuário do portal (`X-Actor-Id`) |
| `futuranext.whatsapp_events`, `futuranext.whatsapp_webhooks` | **Sem alteração.** Continuam recebendo eventos pela rota do portal (`/api/v1/integrations/whatsapp/{tenant}/events`, destino `futuranext` do operador). URL e segredo do bot ficam em `whatsapp.callback_destinations` |

Não confunda o **aceite do administrador** (`futuranext.whatsapp_consents`, termos de uso) com o **opt-out do destinatário** (`whatsapp.suppressions`).

### Módulos removidos do serviço

`auth` (sessão/cookie/CSRF), `accounts`, `billing`, `asaas`, `consent_requests`, `email_service`, `bootstrap` e as migrações Mongo. Antes de remover, confirmei que nenhum endpoint deles é usado:

- O Caddy publica só `/whatsapp/api/v1/messages*` e `/whatsapp/api/v1/instances*`.
- A API do portal chama só `/service/v1/tenants/{id}/whatsapp*`.
- O exemplo e o SDK usam `/api/v1/instances` e `/api/v1/messages/send`.

As rotas públicas, os status HTTP e os formatos JSON foram mantidos. As rotas `/api/v1/api-keys`, `/dashboard`, `/opt-outs` e `/consents` continuam existindo, mas exigem escopos que as chaves emitidas pelo portal não têm (respondem `403`). Antes, só a sessão do painel local, agora removida, chegava a elas.

## 2. Modelo de segurança

- **Papéis:**
  - `wa_api`: backend HTTP.
  - `wa_worker`: fila e entregas.
  - A migração os cria `NOLOGIN`. `python -m app.db_admin setup-whatsapp-roles` ativa `LOGIN`, senha SCRAM e `CONNECTION LIMIT 20`.
  - Nenhum dos dois tem `SUPERUSER`, `BYPASSRLS`, `CREATEROLE`, `CREATEDB` ou herança de papel. O comando recusa papéis com esses atributos.
- **RLS:**
  - Todas as 9 tabelas têm `ENABLE` e `FORCE ROW LEVEL SECURITY`.
  - `wa_api` enxerga apenas `tenant_id = whatsapp.tenant()`. Esse é o valor de `whatsapp.tenant_id` definido pelo serviço em cada transação a partir da **chave de API** (função `api_key_identity`) ou do **token de serviço do portal** (UUID da rota). Nunca vem do navegador.
  - `wa_worker` processa todas as empresas (`USING (true)`) e não tem acesso a `api_keys` nem ao schema `futuranext`.
- **Limites da RLS:** o próprio serviço define `whatsapp.tenant_id`, então a RLS protege contra **falhas de código** (esquecer um filtro), não contra credencial vazada. Por isso as consultas também filtram `tenant_id` explicitamente, e cada processo tem a própria credencial. O dono (`postgres`) e papéis com `BYPASSRLS` ignoram a RLS: reserve-os às migrações.
- **Funções `SECURITY DEFINER`** (com `search_path = ''`, `EXECUTE` só para o papel que precisa):

  | Função | Para quê |
  | --- | --- |
  | `api_key_identity` | Autenticação da chave |
  | `webhook_instance` | Ingresso da Evolution |
  | `terms_accepted` | Aceite dos termos |
  | `tenant_licensed` | Licença vigente |

- **Grants mínimos:**
  - `wa_api` só insere em `gateway_events`. Não lê nem apaga mensagens, e só altera `status`, `error_code` e `updated_at` delas.
  - `wa_api` não toca em `webhook_deliveries`.
- **Data API:**
  - O schema `whatsapp` não está em `[api].schemas` (`supabase/config.toml`, teste automatizado). No projeto de produção, mantenha **Settings › API › Exposed schemas** sem `whatsapp` e sem `evolution`.
  - A migração revoga explicitamente tudo de `anon`, `authenticated` e `service_role`. O teste simula privilégios padrão globais hostis e comprova que nada vaza.
- **Segredos:**
  - Mensagens, telefones, segredos de webhook e o motivo do opt-out são cifrados no serviço com `DATA_ENCRYPTION_KEY` antes de chegar ao banco.
  - Chaves e telefones de busca usam HMAC com `AUTH_PEPPER`.
  - `service_role` nunca é usado.

## 3. Filas, concorrência e retenção

- **Mensagens e entregas:**
  - O claim usa `UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED LIMIT 1)` numa transação curta e grava uma lease (`lease_owner`, `lease_until`).
  - A chamada ao gateway ou ao bot acontece **fora** da transação.
  - A finalização só vale para o dono da lease.
- **Recuperação após reinício:**
  - Uma mensagem com lease vencida e envio iniciado vira `unknown`, nunca reenviada.
  - Uma mensagem sem envio iniciado volta para `queued`.
  - Uma entrega `futuranext` volta para `retry_wait`. Uma entrega `bot` incerta vira `unknown`.
- **Eventos da Evolution:** cada evento é processado na própria transação, bloqueado com `SKIP LOCKED`. As entregas aos webhooks são criadas na **mesma transação** que marca o evento como processado.
- **Idempotência:**
  - Envio: índice único parcial com `ON CONFLICT DO NOTHING`, mais a comparação do `payload_hash`, que responde `409 IDEMPOTENCY_CONFLICT`.
  - Eventos: PK por HMAC.
  - Entregas: ID `uuid5` determinístico.
- **Retenção:** o worker faz o equivalente aos TTLs do Mongo em lotes de 1.000 a cada 30 s.

  | Dado | Retenção |
  | --- | --- |
  | Conteúdo das mensagens | Apagado após 30 dias |
  | Mensagens | 90 dias |
  | Eventos e entregas | 14 dias |
  | Auditoria | 180 dias |

## 4. Validação somente leitura antes de migrar (SQL Editor de produção)

Rode estes `SELECT`s no **SQL Editor** do projeto de produção e guarde o resultado. Nada é alterado. Se algum resultado for diferente do esperado, **não migre**: envie o resultado para análise.

```sql
-- 1. Revisão atual (esperado: 0012_whatsapp_consent)
SELECT version_num FROM futuranext_meta.alembic_version;

-- 2. Nada com os nomes novos existe ainda (esperado: tudo false / 0 linhas)
SELECT to_regnamespace('whatsapp') IS NOT NULL AS schema_whatsapp,
       to_regnamespace('evolution') IS NOT NULL AS schema_evolution;
SELECT rolname FROM pg_roles WHERE rolname IN ('wa_api', 'wa_worker');

-- 3. Tabela de tenants e chaves usadas pelas FKs novas (esperado: organizations_pkey em id)
SELECT conrelid::regclass AS tabela, conname, pg_get_constraintdef(oid) AS definicao
FROM pg_constraint
WHERE conrelid IN ('futuranext.organizations'::regclass, 'futuranext.tenant_products'::regclass,
                   'futuranext.whatsapp_consents'::regclass)
ORDER BY 1, 2;

-- 4. Colunas usadas pelas funções de licença e aceite
SELECT table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'futuranext'
  AND table_name IN ('organizations', 'products', 'tenant_products', 'whatsapp_consents')
ORDER BY table_name, ordinal_position;

-- 5. Produto WhatsApp cadastrado (esperado: 1 linha, active = true)
SELECT id, slug, active FROM futuranext.products WHERE slug = 'whatsapp';

-- 6. RLS e políticas das tabelas reutilizadas
SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, c.relowner::regrole AS dono
FROM pg_class c
WHERE c.oid IN ('futuranext.organizations'::regclass, 'futuranext.tenant_products'::regclass,
                'futuranext.whatsapp_consents'::regclass, 'futuranext.whatsapp_events'::regclass);
SELECT tablename, policyname, roles, cmd FROM pg_policies
WHERE schemaname = 'futuranext' AND tablename LIKE 'whatsapp%' ORDER BY 1, 2;

-- 7. Usuário das migrações (esperado: postgres, dono das tabelas acima)
SELECT current_user, session_user;

-- 8. Schemas expostos pela Data API (esperado: sem whatsapp e sem evolution)
SELECT rolname, rolconfig FROM pg_roles WHERE rolname = 'authenticator';

-- 9. Privilégios padrão que poderiam alcançar objetos novos (informativo; a migração revoga)
SELECT defaclrole::regrole, defaclnamespace::regnamespace, defaclobjtype, defaclacl FROM pg_default_acl;

-- 10. Limite de conexões disponível (compare com o Pool Size em Database › Connection pooling)
SELECT rolname, rolconnlimit FROM pg_roles WHERE rolname IN ('fn_app', 'postgres');
SHOW max_connections;
```

Depois da migração, confira:

```sql
SELECT version_num FROM futuranext_meta.alembic_version;            -- 0013_whatsapp_service
SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class
WHERE relnamespace = 'whatsapp'::regnamespace AND relkind = 'r';    -- 9 linhas, true/true
SELECT r.rolname, has_schema_privilege(r.rolname, 'whatsapp', 'USAGE') AS usa_schema
FROM pg_roles r WHERE r.rolname IN ('anon', 'authenticated', 'service_role', 'fn_app');  -- todos false
SELECT rolname, rolcanlogin, rolbypassrls, rolsuper FROM pg_roles WHERE rolname LIKE 'wa\_%';
```

## 5. Plano de implantação

Nada disso foi executado em produção. Siga na ordem; os passos de produção estão em [WHATSAPP_PUBLICACAO.md](WHATSAPP_PUBLICACAO.md).

1. **Homologação (projeto Supabase separado ou branch):**
   1. Aplique `alembic upgrade head` e rode `setup-whatsapp-roles`.
   2. Rode as suítes `backend/tests` e `whatsapp/tests` apontando para esse banco descartável.
   3. Suba o Compose do WhatsApp com a imagem do commit e faça o fluxo completo: aceite, QR, envio, `SAIR`, webhook do bot.
   4. Confira os SELECTs da seção 4 depois da migração.
2. **Validação de produção:** os SELECTs da seção 4 (antes) devem dar o esperado.
3. **Backup:** `pg_dump` dos schemas `auth`, `futuranext` e `futuranext_meta` (passo W2 do guia) e cópia do `.env` do WhatsApp.
4. **Migração:** `alembic upgrade head` (0012 → 0013) com o papel de migração, depois `setup-whatsapp-roles`. A migração só **cria** objetos novos e não altera tabelas existentes.
   > ⚠️ A prontidão da API do portal exige a revisão exata. Entre a migração e a troca da imagem da API (passo 6), `futuranext-api-1` fica **unhealthy** para o Docker, sem parar de responder. Faça os passos 4 a 6 em sequência, na mesma sessão.
5. **Imagens:** `futuranext-api:<commit>` e `futuranext-whatsapp:<commit>`, ambas construídas do mesmo commit.
6. **Deploy:**
   1. Suba o WhatsApp (`backend`, `worker`, Redis e Evolution).
   2. Troque a imagem da API do portal com as variáveis `FN_WHATSAPP_*`.
   3. Atualize o Caddy por último.
   4. `futuranext-proxy-1` só é reiniciado no último passo, depois de `caddy validate`.
7. **Testes pós-deploy:**
   - `health/ready` da API e de `whatsapp-api` respondem bem.
   - `WHATSAPP_PUBLICO 401` e `SERVICO_INTERNO 404`.
   - Os SELECTs pós-migração da seção 4 dão o esperado.
   - Fluxo com a empresa de teste.
8. **Rollback**, do mais simples para o mais drástico:
   1. **Caddy:** volte o `Caddyfile.before`.
   2. **WhatsApp:** `docker compose … down` no projeto `futuranext-whatsapp`. Não usa `-v`, então os volumes do Redis e da Evolution ficam.
   3. **API do portal:** só volte a imagem anterior depois de reverter a migração. A imagem antiga exige `0012`.
   4. **Banco:** `alembic downgrade 0012_whatsapp_consent` remove só os objetos do schema `whatsapp`, um a um, sem `CASCADE`, e **apaga os dados do serviço**. Depois rode `ALTER ROLE wa_api NOLOGIN; ALTER ROLE wa_worker NOLOGIN;`. Em último caso, restaure o `pre-deploy.dump`.

## 6. Evolution API: PostgreSQL próprio (temporário)

**Situação atual: a Evolution continua com o PostgreSQL local `evolution-postgres` (volume no Droplet). Ela NÃO está no Supabase.** Isso é temporário e está identificado no `whatsapp/compose.yaml`.

Por que não foi movida agora:

- **Migrações automáticas:** o `ENTRYPOINT` da imagem `evoapicloud/evolution-api:v2.3.7` é `. ./Docker/scripts/deploy_database.sh && npm run start:prod`, conferido com `docker image inspect` em 18/11/2026. O script roda `npm run db:deploy` (Prisma Migrate) a cada início. Apontá-la para o Supabase de produção faria uma ferramenta de terceiros alterar schema em produção a cada reinício ou atualização. Isso é exatamente o que este trabalho deve evitar.
- **Senha nos logs:** o mesmo script executa `echo "Database URL: $DATABASE_URL"`, que imprime a URL **com a senha** nos logs do contêiner. Com o PostgreSQL local, isso expõe só a senha do banco interno, sem porta publicada. Com o Supabase, exporia a credencial do projeto de produção nos logs do Docker. **Impeditivo:** antes de qualquer uso com o Supabase, é preciso um entrypoint próprio que não imprima a URL.
- O Prisma Migrate precisa de conexão direta ou de **Session pooler**. Não funciona pelo Transaction pooler (6543) sem `directUrl`. No Droplet, que só tem IPv4, só o Session pooler serve. Esse pooler é o mesmo usado pela API do portal e pelo serviço WhatsApp, e o *Pool Size* do projeto é compartilhado entre todos.
- O papel da Evolution precisa de `CREATE` no próprio schema. É um privilégio maior do que o de `wa_api`/`wa_worker`, e precisa ficar confinado ao schema `evolution`.

Avaliação em **homologação** (não executada):

1. **Banco de homologação:** crie um projeto Supabase de homologação, nunca o de produção.
2. **Schema e papel:**
   ```sql
   CREATE SCHEMA evolution;
   CREATE ROLE evolution_app LOGIN PASSWORD '…' NOINHERIT;
   ALTER SCHEMA evolution OWNER TO evolution_app;
   REVOKE ALL ON SCHEMA evolution FROM PUBLIC, anon, authenticated, service_role;
   ```
   Nenhum privilégio em `futuranext` ou `whatsapp`.
3. **Conexão:** `DATABASE_CONNECTION_URI=postgresql://evolution_app.REF:…@HOST_POOLER:5432/postgres?schema=evolution&sslmode=require`, pelo Session pooler e com TLS.
4. **Subida:** suba a Evolution 2.3.7 e confira:
   - as migrações Prisma terminam;
   - um número pareia e sobrevive a reinício;
   - o consumo de conexões cabe no *Pool Size*;
   - nada aparece fora do schema `evolution`.
5. **Rollback em homologação:** derrube a Evolution e restaure o volume local.
6. **Decisão:** só com tudo aprovado. Em produção, rode as migrações Prisma separadamente, com backup e janela, nunca no start automático.

### Checklist

| Item | Situação |
| --- | --- |
| Dados do serviço WhatsApp no Supabase (schema `whatsapp`) | ✅ Implementado e testado em PostgreSQL 17 local |
| MongoDB removido do código, das dependências e do Compose | ✅ |
| Evolution no Supabase (schema `evolution`) | ❌ **Não.** Continua no PostgreSQL local, temporário. Depende da homologação acima e de um entrypoint que não registre a URL do banco |
| Portas de Redis/PostgreSQL publicadas | ❌ Nenhuma, como antes |
| Schemas `whatsapp`/`evolution` expostos na Data API | ❌ Não, e há teste para o arquivo de configuração |

## 7. Testes

**Como rodar.** A suíte usa PostgreSQL real e as migrações Alembic do portal:

```bash
cd whatsapp
WA_TEST_MIGRATION_DATABASE_URL=postgresql://postgres:SENHA@127.0.0.1:55432/futuranext_test python -m pytest -q
```

O ambiente precisa de `backend/requirements.lock`, `backend/requirements-dev.txt` e `pip install ../backend`, para o Alembic. Sem a variável, só os 21 testes sem banco rodam.

**O que a suíte cobre:**

- migração reversível (`upgrade` → `downgrade` → `upgrade`), papéis, RLS, grants e privilégios padrão hostis;
- Data API, `/health/ready`, criação e nova tentativa de instância, QR com aceite;
- envio, idempotência (também com 8 requisições simultâneas), recebimento e status, opt-out por `SAIR`;
- HMAC dos webhooks, retries com backoff, recuperação após reinício, 4 workers concorrentes (envio único) e entregas concorrentes;
- isolamento entre empresas (A não lê B, nem por FK), licença encerrada e limite de chaves sob concorrência;
- retenção.
