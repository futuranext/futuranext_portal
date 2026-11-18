-- Dados operacionais do serviço WhatsApp (antes no MongoDB), no mesmo PostgreSQL do portal.
--
-- * Schema privado `whatsapp`: não é exposto na Data API (não está em [api].schemas) e nenhum
--   papel do Supabase (anon, authenticated, service_role) recebe privilégio nele.
-- * Papéis de runtime: wa_api (backend HTTP) e wa_worker (fila e entregas). São criados aqui
--   sem LOGIN; `python -m app.db_admin setup-whatsapp-roles` define senha e LOGIN.
-- * wa_api só enxerga a empresa da transação (`whatsapp.tenant_id`, definido pelo serviço a partir
--   da chave de API ou do token do portal). Consultas que precisam localizar a empresa usam
--   funções SECURITY DEFINER pequenas e específicas.
-- * wa_worker processa filas de todas as empresas (RLS com USING true) e não acessa o portal.
-- * Licença, empresa e aceite continuam no schema futuranext e são consultados por funções.
-- * Retenção equivalente aos TTLs do MongoDB: expires_at + limpeza periódica pelo worker.

CREATE SCHEMA whatsapp;
REVOKE ALL ON SCHEMA whatsapp FROM PUBLIC;

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'wa_api') THEN
        CREATE ROLE wa_api NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'wa_worker') THEN
        CREATE ROLE wa_worker NOLOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA whatsapp TO wa_api, wa_worker;

CREATE FUNCTION whatsapp.tenant() RETURNS uuid
LANGUAGE sql STABLE SET search_path = '' AS $$
    SELECT nullif(current_setting('whatsapp.tenant_id', true), '')::uuid
$$;

-- Instâncias (antes: instances). Uma linha por número pareado na Evolution.
CREATE TABLE whatsapp.instances (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES futuranext.organizations(id) ON DELETE CASCADE,
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
    client_label text NOT NULL DEFAULT '' CHECK (length(client_label) <= 100),
    provider text NOT NULL DEFAULT 'evolution' CHECK (provider = 'evolution'),
    provider_ref text NOT NULL UNIQUE CHECK (provider_ref ~ '^wp_[0-9a-f-]{36}$'),
    connection_state text NOT NULL CHECK (connection_state IN (
        'creating', 'disconnected', 'connecting', 'connected', 'expired', 'error', 'deleting')),
    phone_masked text CHECK (length(phone_masked) <= 20),
    connected_phone text CHECK (length(connected_phone) <= 1000),
    webhook_hash text CHECK (webhook_hash ~ '^[0-9a-f]{64}$'),
    webhook_secret text CHECK (length(webhook_secret) <= 1000),
    last_error text CHECK (length(last_error) <= 80),
    last_connected_at timestamptz,
    last_disconnected_at timestamptz,
    observed_at timestamptz,
    last_event_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name),
    UNIQUE (tenant_id, id)
);
CREATE INDEX instances_by_tenant ON whatsapp.instances (tenant_id, created_at);
CREATE INDEX instances_monitored ON whatsapp.instances (created_at)
    WHERE connection_state NOT IN ('creating', 'error', 'deleting');

-- Chaves de API (antes: api_keys). Somente o HMAC do segredo é guardado.
CREATE TABLE whatsapp.api_keys (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES futuranext.organizations(id) ON DELETE CASCADE,
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
    prefix text NOT NULL CHECK (length(prefix) = 10),
    secret_hash text NOT NULL UNIQUE CHECK (secret_hash ~ '^[0-9a-f]{64}$'),
    scopes text[] NOT NULL CHECK (
        cardinality(scopes) BETWEEN 1 AND 3
        AND scopes <@ ARRAY['instances:read', 'messages:read', 'messages:send']::text[]),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL CHECK (expires_at > created_at),
    revoked_at timestamptz,
    UNIQUE (tenant_id, id)
);
CREATE INDEX api_keys_by_tenant ON whatsapp.api_keys (tenant_id, created_at DESC);

-- Instâncias liberadas para cada chave (antes: api_keys.instance_ids). FKs compostas impedem
-- associar uma chave a instância de outra empresa.
CREATE TABLE whatsapp.api_key_instances (
    tenant_id uuid NOT NULL,
    key_id uuid NOT NULL,
    instance_id uuid NOT NULL,
    PRIMARY KEY (key_id, instance_id),
    FOREIGN KEY (tenant_id, key_id) REFERENCES whatsapp.api_keys (tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, instance_id) REFERENCES whatsapp.instances (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX api_key_instances_by_instance ON whatsapp.api_key_instances (tenant_id, instance_id);

-- Mensagens de saída (fila) e de entrada. Telefone e texto ficam cifrados em `content`.
-- Sem FK para instances: o histórico continua consultável até expirar após excluir a instância.
CREATE TABLE whatsapp.messages (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES futuranext.organizations(id) ON DELETE CASCADE,
    instance_id uuid NOT NULL,
    direction text NOT NULL CHECK (direction IN ('outbound', 'inbound')),
    status text NOT NULL CHECK (status IN (
        'queued', 'processing', 'retry_wait', 'sent', 'delivered', 'read', 'received',
        'failed', 'unknown', 'cancelled')),
    idempotency_key text CHECK (idempotency_key ~ '^[\x21-\x7e]{1,128}$'),
    payload_hash text CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    phone_hash text NOT NULL CHECK (phone_hash ~ '^[0-9a-f]{64}$'),
    content text CHECK (length(content) <= 20000),
    provider_message_id text CHECK (length(provider_message_id) <= 200),
    error_code text CHECK (length(error_code) <= 80),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    due_at timestamptz,
    lease_owner uuid,
    lease_until timestamptz,
    dispatch_started_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz,
    content_expires_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    CHECK (direction = 'inbound' OR (idempotency_key IS NOT NULL AND payload_hash IS NOT NULL)),
    CHECK (direction = 'outbound' OR provider_message_id IS NOT NULL)
);
CREATE UNIQUE INDEX messages_idempotency ON whatsapp.messages (tenant_id, instance_id, idempotency_key)
    WHERE direction = 'outbound';
CREATE UNIQUE INDEX messages_inbound_once ON whatsapp.messages (tenant_id, instance_id, provider_message_id)
    WHERE direction = 'inbound';
CREATE INDEX messages_outbound_provider ON whatsapp.messages (tenant_id, instance_id, provider_message_id)
    WHERE direction = 'outbound' AND provider_message_id IS NOT NULL;
CREATE INDEX messages_queue ON whatsapp.messages (due_at, created_at)
    WHERE status IN ('queued', 'retry_wait');
CREATE INDEX messages_leases ON whatsapp.messages (lease_until) WHERE status = 'processing';
CREATE INDEX messages_recent ON whatsapp.messages (tenant_id, created_at DESC, id DESC);
CREATE INDEX messages_by_phone ON whatsapp.messages (tenant_id, phone_hash)
    WHERE status IN ('queued', 'retry_wait');
CREATE INDEX messages_expiry ON whatsapp.messages (expires_at);
CREATE INDEX messages_content_expiry ON whatsapp.messages (content_expires_at) WHERE content IS NOT NULL;

-- Opt-out de destinatários (antes: suppressions). Não confundir com o aceite do administrador,
-- que fica em futuranext.whatsapp_consents.
CREATE TABLE whatsapp.suppressions (
    tenant_id uuid NOT NULL REFERENCES futuranext.organizations(id) ON DELETE CASCADE,
    phone_hash text NOT NULL CHECK (phone_hash ~ '^[0-9a-f]{64}$'),
    opted_out_at timestamptz NOT NULL DEFAULT now(),
    reason text CHECK (length(reason) <= 2000),
    PRIMARY KEY (tenant_id, phone_hash)
);

-- Eventos recebidos da Evolution (antes: gateway_events). event_key é o HMAC do evento.
CREATE TABLE whatsapp.gateway_events (
    event_key text PRIMARY KEY CHECK (event_key ~ '^[0-9a-f]{64}$'),
    tenant_id uuid NOT NULL,
    instance_id uuid NOT NULL,
    type text NOT NULL CHECK (type IN ('connection.update', 'messages.upsert', 'messages.update')),
    data text NOT NULL CHECK (length(data) <= 20000),
    processed boolean NOT NULL DEFAULT false,
    outcome text CHECK (outcome IN ('awaiting_correlation', 'unmatched')),
    next_attempt_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    UNIQUE (tenant_id, instance_id, event_key),
    FOREIGN KEY (tenant_id, instance_id) REFERENCES whatsapp.instances (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX gateway_events_pending ON whatsapp.gateway_events (created_at) WHERE NOT processed;
CREATE INDEX gateway_events_expiry ON whatsapp.gateway_events (expires_at);

-- Webhook do bot por instância (antes: callback_destinations). URL e segredo cifrado ficam
-- aqui; futuranext.whatsapp_webhooks continua com o contrato do portal (4 colunas).
CREATE TABLE whatsapp.callback_destinations (
    tenant_id uuid NOT NULL,
    instance_id uuid NOT NULL,
    kind text NOT NULL DEFAULT 'bot' CHECK (kind = 'bot'),
    url text NOT NULL CHECK (length(url) BETWEEN 12 AND 2048),
    include_message boolean NOT NULL DEFAULT false,
    secret text NOT NULL CHECK (length(secret) <= 2000),
    enabled boolean NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, instance_id, kind),
    FOREIGN KEY (tenant_id, instance_id) REFERENCES whatsapp.instances (tenant_id, id) ON DELETE CASCADE
);

-- Entregas assinadas aos bots e ao portal (antes: webhook_deliveries).
CREATE TABLE whatsapp.webhook_deliveries (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    instance_id uuid NOT NULL,
    source_event text NOT NULL,
    destination_key text NOT NULL CHECK (destination_key ~ '^[0-9a-f]{64}$'),
    kind text NOT NULL CHECK (kind IN ('bot', 'futuranext')),
    event_id uuid NOT NULL,
    event_type text NOT NULL CHECK (event_type IN ('message.received', 'message.status', 'instance.connection')),
    occurred_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN (
        'queued', 'processing', 'retry_wait', 'delivered', 'failed', 'unknown', 'cancelled')),
    due_at timestamptz NOT NULL,
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    lease_owner uuid,
    lease_until timestamptz,
    error_code text CHECK (length(error_code) <= 80),
    http_status integer CHECK (http_status BETWEEN 100 AND 599),
    updated_at timestamptz,
    expires_at timestamptz NOT NULL,
    FOREIGN KEY (tenant_id, instance_id, source_event)
        REFERENCES whatsapp.gateway_events (tenant_id, instance_id, event_key) ON DELETE CASCADE
);
CREATE INDEX webhook_deliveries_queue ON whatsapp.webhook_deliveries (due_at)
    WHERE status IN ('queued', 'retry_wait');
CREATE INDEX webhook_deliveries_leases ON whatsapp.webhook_deliveries (lease_until) WHERE status = 'processing';
CREATE INDEX webhook_deliveries_source ON whatsapp.webhook_deliveries (tenant_id, instance_id, source_event);
CREATE INDEX webhook_deliveries_expiry ON whatsapp.webhook_deliveries (expires_at);

-- Auditoria operacional (antes: audit_events), retenção de 180 dias.
CREATE TABLE whatsapp.audit_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES futuranext.organizations(id) ON DELETE CASCADE,
    actor_id text NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 100),
    action text NOT NULL CHECK (length(action) BETWEEN 1 AND 80),
    resource_id text NOT NULL DEFAULT '' CHECK (length(resource_id) <= 100),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL DEFAULT now() + interval '180 days'
);
CREATE INDEX audit_events_recent ON whatsapp.audit_events (tenant_id, created_at DESC);
CREATE INDEX audit_events_expiry ON whatsapp.audit_events (expires_at);

-- RLS em todas as tabelas: dono (migrações e funções), wa_api por empresa, wa_worker global.
DO $$
DECLARE
    relation text;
BEGIN
    FOREACH relation IN ARRAY ARRAY[
        'instances', 'api_keys', 'api_key_instances', 'messages', 'suppressions', 'gateway_events',
        'callback_destinations', 'webhook_deliveries', 'audit_events'
    ] LOOP
        EXECUTE format('ALTER TABLE whatsapp.%I ENABLE ROW LEVEL SECURITY', relation);
        EXECUTE format('ALTER TABLE whatsapp.%I FORCE ROW LEVEL SECURITY', relation);
        EXECUTE format('CREATE POLICY owner_operations ON whatsapp.%I TO %I USING (true) WITH CHECK (true)',
                       relation, current_user);
        EXECUTE format('CREATE POLICY api_tenant ON whatsapp.%I TO wa_api '
                       'USING (tenant_id = whatsapp.tenant()) WITH CHECK (tenant_id = whatsapp.tenant())',
                       relation);
        EXECUTE format('CREATE POLICY worker_all ON whatsapp.%I TO wa_worker USING (true) WITH CHECK (true)',
                       relation);
    END LOOP;
END
$$;

-- Licença ativa da ferramenta WhatsApp, com as mesmas regras de futuranext.can_use_tool
-- (sem a parte de usuário, que o serviço não tem).
CREATE FUNCTION whatsapp.tenant_licensed(org uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
    SELECT EXISTS (
        SELECT FROM futuranext.organizations organization
        JOIN futuranext.tenant_products license ON license.tenant_id = organization.id
        JOIN futuranext.products product ON product.id = license.product_id
        WHERE organization.id = org AND organization.active
          AND product.slug = 'whatsapp' AND product.active
          AND license.active AND license.starts_at <= now()
          AND (license.ends_at IS NULL OR license.ends_at > now())
    )
$$;

-- Autenticação por chave: devolve a chave ativa somente se a empresa estiver licenciada.
CREATE FUNCTION whatsapp.api_key_identity(digest text)
RETURNS TABLE (id uuid, tenant_id uuid, scopes text[], instance_ids uuid[])
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
    SELECT key.id, key.tenant_id, key.scopes,
           coalesce(array_agg(link.instance_id ORDER BY link.instance_id)
                    FILTER (WHERE link.instance_id IS NOT NULL), '{}')
    FROM whatsapp.api_keys key
    LEFT JOIN whatsapp.api_key_instances link ON link.key_id = key.id AND link.tenant_id = key.tenant_id
    WHERE key.secret_hash = digest AND key.revoked_at IS NULL AND key.expires_at > now()
      AND whatsapp.tenant_licensed(key.tenant_id)
    GROUP BY key.id, key.tenant_id, key.scopes
$$;

-- Ingresso da Evolution: localiza a empresa da instância para validar o segredo do webhook.
CREATE FUNCTION whatsapp.webhook_instance(instance uuid)
RETURNS TABLE (tenant_id uuid, provider_ref text, webhook_hash text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
    SELECT item.tenant_id, item.provider_ref, item.webhook_hash
    FROM whatsapp.instances item WHERE item.id = instance
$$;

-- Aceite dos termos pelo usuário do portal que pareia o número (futuranext.whatsapp_consents).
CREATE FUNCTION whatsapp.terms_accepted(org uuid, actor uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
    SELECT EXISTS (
        SELECT FROM futuranext.whatsapp_consents consent
        WHERE consent.tenant_id = org AND consent.user_id = actor AND consent.accepted_at IS NOT NULL
    )
$$;

-- Grants mínimos.
REVOKE ALL ON ALL TABLES IN SCHEMA whatsapp FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA whatsapp FROM PUBLIC;

GRANT SELECT, INSERT, UPDATE, DELETE ON whatsapp.instances TO wa_api;
GRANT SELECT, INSERT ON whatsapp.api_keys, whatsapp.api_key_instances TO wa_api;
GRANT UPDATE (revoked_at) ON whatsapp.api_keys TO wa_api;
GRANT SELECT, INSERT ON whatsapp.messages TO wa_api;
GRANT UPDATE (status, error_code, updated_at) ON whatsapp.messages TO wa_api;
GRANT SELECT, INSERT ON whatsapp.suppressions TO wa_api;
GRANT UPDATE (opted_out_at, reason) ON whatsapp.suppressions TO wa_api;
GRANT INSERT ON whatsapp.gateway_events TO wa_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON whatsapp.callback_destinations TO wa_api;
GRANT SELECT, INSERT ON whatsapp.audit_events TO wa_api;

GRANT SELECT, UPDATE ON whatsapp.instances TO wa_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON whatsapp.messages TO wa_worker;
GRANT SELECT, INSERT, UPDATE ON whatsapp.suppressions TO wa_worker;
GRANT SELECT, UPDATE, DELETE ON whatsapp.gateway_events TO wa_worker;
GRANT SELECT ON whatsapp.callback_destinations TO wa_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON whatsapp.webhook_deliveries TO wa_worker;
GRANT SELECT, INSERT, DELETE ON whatsapp.audit_events TO wa_worker;

GRANT EXECUTE ON FUNCTION whatsapp.tenant() TO wa_api, wa_worker;
GRANT EXECUTE ON FUNCTION whatsapp.tenant_licensed(uuid) TO wa_api, wa_worker;
GRANT EXECUTE ON FUNCTION whatsapp.api_key_identity(text), whatsapp.webhook_instance(uuid),
    whatsapp.terms_accepted(uuid, uuid) TO wa_api;

-- Supabase: garante que nenhum privilégio padrão alcance os papéis da Data API.
DO $$
DECLARE
    api_role text;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format('REVOKE ALL ON SCHEMA whatsapp FROM %I', api_role);
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA whatsapp FROM %I', api_role);
            EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA whatsapp FROM %I', api_role);
            EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA whatsapp FROM %I', api_role);
        END IF;
    END LOOP;
END
$$;
