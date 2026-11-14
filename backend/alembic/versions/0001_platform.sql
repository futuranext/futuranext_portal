CREATE SCHEMA futuranext;
REVOKE ALL ON SCHEMA futuranext, futuranext_meta FROM PUBLIC;
GRANT USAGE ON SCHEMA futuranext, futuranext_meta TO fn_app;
GRANT SELECT ON futuranext_meta.alembic_version TO fn_app;

CREATE TABLE futuranext.roles (
    code text PRIMARY KEY
);

CREATE TABLE futuranext.permissions (
    code text PRIMARY KEY
);

CREATE TABLE futuranext.role_permissions (
    role text NOT NULL REFERENCES futuranext.roles(code),
    permission text NOT NULL REFERENCES futuranext.permissions(code),
    PRIMARY KEY (role, permission)
);

CREATE TABLE futuranext.organizations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
    slug text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND length(slug) <= 64),
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE futuranext.profiles (
    user_id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email text NOT NULL CHECK (email = lower(btrim(email)) AND length(email) <= 254),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE futuranext.platform_roles (
    user_id uuid PRIMARY KEY REFERENCES futuranext.profiles(user_id) ON DELETE CASCADE,
    role text NOT NULL REFERENCES futuranext.roles(code) CHECK (role IN ('super_admin', 'suporte'))
);

CREATE UNIQUE INDEX one_initial_admin ON futuranext.platform_roles (role) WHERE role = 'super_admin';

CREATE TABLE futuranext.organization_memberships (
    tenant_id uuid REFERENCES futuranext.organizations(id),
    user_id uuid REFERENCES futuranext.profiles(user_id) ON DELETE CASCADE,
    role text NOT NULL REFERENCES futuranext.roles(code)
        CHECK (role IN ('cliente_admin', 'cliente_usuario')),
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_id)
);

CREATE INDEX memberships_by_user ON futuranext.organization_memberships (user_id, tenant_id)
    WHERE active;

CREATE TABLE futuranext.support_assignments (
    tenant_id uuid REFERENCES futuranext.organizations(id),
    user_id uuid REFERENCES futuranext.platform_roles(user_id) ON DELETE CASCADE,
    active boolean NOT NULL DEFAULT true,
    PRIMARY KEY (tenant_id, user_id)
);

CREATE TABLE futuranext.products (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND length(slug) <= 64),
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
    description text NOT NULL DEFAULT '' CHECK (length(description) <= 1000),
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE futuranext.tenant_products (
    tenant_id uuid REFERENCES futuranext.organizations(id),
    product_id uuid REFERENCES futuranext.products(id),
    active boolean NOT NULL DEFAULT true,
    starts_at timestamptz NOT NULL DEFAULT now(),
    ends_at timestamptz CHECK (ends_at > starts_at),
    PRIMARY KEY (tenant_id, product_id)
);

CREATE TABLE futuranext.user_permissions (
    tenant_id uuid NOT NULL,
    user_id uuid NOT NULL,
    product_id uuid NOT NULL,
    permission text NOT NULL REFERENCES futuranext.permissions(code),
    allowed boolean NOT NULL,
    PRIMARY KEY (tenant_id, user_id, product_id, permission),
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES futuranext.organization_memberships(tenant_id, user_id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, product_id)
        REFERENCES futuranext.tenant_products(tenant_id, product_id) ON DELETE CASCADE
);

CREATE TABLE futuranext.invitations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES futuranext.organizations(id),
    email text NOT NULL CHECK (email = lower(btrim(email)) AND length(email) <= 254),
    role text NOT NULL REFERENCES futuranext.roles(code) CHECK (role IN ('cliente_admin', 'cliente_usuario')),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL DEFAULT now() + interval '7 days',
    accepted_at timestamptz,
    accepted_by uuid REFERENCES futuranext.profiles(user_id),
    delivery text NOT NULL DEFAULT 'pending' CHECK (delivery IN ('pending', 'sent', 'existing_account')),
    delivery_claim_until timestamptz,
    CHECK ((accepted_at IS NULL) = (accepted_by IS NULL))
);

CREATE UNIQUE INDEX pending_invitation_per_email ON futuranext.invitations (tenant_id, email)
    WHERE accepted_at IS NULL;
CREATE INDEX invitations_by_email ON futuranext.invitations (email) WHERE accepted_at IS NULL;

CREATE TABLE futuranext.audit_logs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    actor_id uuid,
    tenant_id uuid,
    resource text NOT NULL,
    operation text NOT NULL CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE'))
);

CREATE INDEX audit_recent ON futuranext.audit_logs (occurred_at DESC);

INSERT INTO futuranext.roles VALUES ('super_admin'), ('suporte'), ('cliente_admin'), ('cliente_usuario');
INSERT INTO futuranext.permissions VALUES
    ('admin.access'), ('admin.tenant.read'), ('admin.tenant.manage'), ('admin.users.read'),
    ('admin.users.manage'), ('admin.product.manage'), ('admin.entitlement.manage'),
    ('admin.audit.read'), ('tenant.access'), ('product.view');

INSERT INTO futuranext.role_permissions (role, permission)
    SELECT 'super_admin', code FROM futuranext.permissions WHERE code LIKE 'admin.%';
INSERT INTO futuranext.role_permissions VALUES
    ('suporte', 'admin.access'), ('suporte', 'admin.tenant.read'), ('suporte', 'admin.users.read'),
    ('cliente_admin', 'admin.users.read'), ('cliente_admin', 'admin.users.manage'),
    ('cliente_admin', 'tenant.access'), ('cliente_admin', 'product.view'),
    ('cliente_usuario', 'tenant.access'), ('cliente_usuario', 'product.view');

INSERT INTO futuranext.products (slug, name, description) VALUES
    ('barbearia', 'Chatbot para Barbearias', 'Configuração de atendimento e agendamento para barbearias.'),
    ('clinica', 'Chatbot para Clínicas de Estética', 'Configuração de atendimento e agenda de clínicas.'),
    ('nfse', 'Emissor de NFS-e em lote', 'Validação de planilhas e acompanhamento de lotes fiscais.');

-- O usuário verificado pela API é o único contexto que o runtime aplica.
CREATE FUNCTION futuranext.actor() RETURNS uuid LANGUAGE sql STABLE SET search_path = ''
AS $$ SELECT NULLIF(current_setting('fn.actor_id', true), '')::uuid $$;

CREATE FUNCTION futuranext.tenant() RETURNS uuid LANGUAGE sql STABLE SET search_path = ''
AS $$ SELECT NULLIF(current_setting('fn.tenant_id', true), '')::uuid $$;

CREATE FUNCTION futuranext.has_mfa() RETURNS boolean LANGUAGE sql STABLE SET search_path = ''
AS $$ SELECT COALESCE(current_setting('fn.aal', true) = 'aal2', false) $$;

CREATE FUNCTION futuranext.platform_role() RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = ''
AS $$
    SELECT role FROM futuranext.platform_roles WHERE user_id = futuranext.actor()
$$;

CREATE FUNCTION futuranext.member_role(org uuid) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = ''
AS $$
    SELECT role FROM futuranext.organization_memberships
    WHERE tenant_id = org AND user_id = futuranext.actor() AND active
$$;

CREATE FUNCTION futuranext.tenant_active(org uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = ''
AS $$
    SELECT EXISTS (SELECT FROM futuranext.organizations WHERE id = org AND active)
$$;

CREATE FUNCTION futuranext.assigned_support(org uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = ''
AS $$
    SELECT futuranext.has_mfa() AND futuranext.platform_role() = 'suporte'
        AND EXISTS (
            SELECT FROM futuranext.support_assignments
            WHERE tenant_id = org AND user_id = futuranext.actor() AND active
        )
$$;

CREATE FUNCTION futuranext.confirmed_email() RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = ''
AS $$
    SELECT lower(btrim(email)) FROM auth.users
    WHERE id = futuranext.actor() AND email_confirmed_at IS NOT NULL
$$;

CREATE FUNCTION futuranext.has_permission(required text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = ''
AS $$
    SELECT EXISTS (
        SELECT FROM futuranext.role_permissions perm
        WHERE perm.permission = required AND (
            (
                futuranext.has_mfa()
                AND perm.role = futuranext.platform_role()
                AND (
                    perm.role = 'super_admin'
                    OR required = 'admin.access'
                    OR futuranext.assigned_support(futuranext.tenant())
                )
            )
            OR (
                futuranext.tenant_active(futuranext.tenant())
                AND perm.role = futuranext.member_role(futuranext.tenant())
                AND (required <> 'admin.users.manage' OR futuranext.has_mfa())
            )
        )
    )
$$;

CREATE FUNCTION futuranext.can_read_members(org uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path = ''
AS $$
    SELECT org = futuranext.tenant() AND futuranext.has_permission('admin.users.read')
$$;

CREATE FUNCTION futuranext.can_manage_member(org uuid, target_role text) RETURNS boolean
LANGUAGE sql STABLE SET search_path = ''
AS $$
    SELECT org = futuranext.tenant()
        AND futuranext.has_permission('admin.users.manage')
        AND (target_role = 'cliente_usuario' OR futuranext.has_permission('admin.tenant.manage'))
$$;

CREATE FUNCTION futuranext.can_view_product(org uuid, item uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = ''
AS $$
    SELECT org = futuranext.tenant() AND futuranext.has_permission('product.view')
       AND COALESCE((
            SELECT allowed FROM futuranext.user_permissions
            WHERE tenant_id = org AND user_id = futuranext.actor() AND product_id = item
                AND permission = 'product.view'
       ), true)
$$;

-- Rotinas SECURITY DEFINER têm pesquisa de nomes controlada e escopo de dados explícito.
-- As políticas exclusivas do proprietário dão acesso pontual a helpers, bootstrap e audit.
DO $$
DECLARE
    relation text;
BEGIN
    FOREACH relation IN ARRAY ARRAY[
        'organizations', 'profiles', 'platform_roles', 'organization_memberships',
        'support_assignments', 'products', 'tenant_products', 'user_permissions',
        'invitations', 'audit_logs'
    ] LOOP
        EXECUTE format('ALTER TABLE futuranext.%I ENABLE ROW LEVEL SECURITY', relation);
        EXECUTE format('ALTER TABLE futuranext.%I FORCE ROW LEVEL SECURITY', relation);
        EXECUTE format(
            'CREATE POLICY owner_operations ON futuranext.%I TO %I USING (true) WITH CHECK (true)',
            relation, current_user
        );
    END LOOP;
END
$$;

CREATE POLICY organization_read ON futuranext.organizations FOR SELECT TO fn_app USING (
    (futuranext.tenant() IS NULL OR id = futuranext.tenant())
    AND (
        futuranext.has_permission('admin.tenant.manage')
        OR (active AND futuranext.member_role(id) IS NOT NULL)
        OR futuranext.assigned_support(id)
    )
);
CREATE POLICY organization_create ON futuranext.organizations FOR INSERT TO fn_app WITH CHECK (
    futuranext.tenant() IS NULL AND futuranext.has_permission('admin.tenant.manage')
);
CREATE POLICY organization_update ON futuranext.organizations FOR UPDATE TO fn_app USING (
    id = futuranext.tenant() AND futuranext.has_permission('admin.tenant.manage')
) WITH CHECK (id = futuranext.tenant() AND futuranext.has_permission('admin.tenant.manage'));

CREATE POLICY platform_roles_read ON futuranext.platform_roles FOR SELECT TO fn_app
    USING (user_id = futuranext.actor());

CREATE POLICY profile_read ON futuranext.profiles FOR SELECT TO fn_app USING (
    user_id = futuranext.actor()
    OR EXISTS (
        SELECT FROM futuranext.organization_memberships AS member
        WHERE member.user_id = profiles.user_id AND futuranext.can_read_members(member.tenant_id)
    )
);

CREATE POLICY membership_read ON futuranext.organization_memberships FOR SELECT TO fn_app USING (
    (user_id = futuranext.actor() AND active AND (futuranext.tenant() IS NULL OR tenant_id = futuranext.tenant()))
    OR futuranext.can_read_members(tenant_id)
);
CREATE POLICY membership_update ON futuranext.organization_memberships FOR UPDATE TO fn_app
    USING (futuranext.can_manage_member(tenant_id, role))
    WITH CHECK (futuranext.can_manage_member(tenant_id, role));

CREATE POLICY assignment_read ON futuranext.support_assignments FOR SELECT TO fn_app USING (
    futuranext.has_permission('admin.tenant.manage') OR
    (active AND user_id = futuranext.actor() AND futuranext.assigned_support(tenant_id))
);

CREATE POLICY product_read ON futuranext.products FOR SELECT TO fn_app USING (
    futuranext.has_permission('admin.product.manage')
    OR (
        active AND EXISTS (
            SELECT FROM futuranext.tenant_products AS license
            WHERE license.product_id = products.id AND license.tenant_id = futuranext.tenant()
                AND license.active AND license.starts_at <= now()
                AND (license.ends_at IS NULL OR license.ends_at > now())
        )
    )
);
CREATE POLICY product_create ON futuranext.products FOR INSERT TO fn_app
    WITH CHECK (futuranext.tenant() IS NULL AND futuranext.has_permission('admin.product.manage'));
CREATE POLICY product_update ON futuranext.products FOR UPDATE TO fn_app
    USING (futuranext.tenant() IS NULL AND futuranext.has_permission('admin.product.manage'))
    WITH CHECK (futuranext.tenant() IS NULL AND futuranext.has_permission('admin.product.manage'));

CREATE POLICY license_read ON futuranext.tenant_products FOR SELECT TO fn_app USING (
    tenant_id = futuranext.tenant() AND (
        futuranext.has_permission('admin.entitlement.manage')
        OR (
            active AND starts_at <= now() AND (ends_at IS NULL OR ends_at > now())
            AND futuranext.can_view_product(tenant_id, product_id)
        )
    )
);
CREATE POLICY license_create ON futuranext.tenant_products FOR INSERT TO fn_app WITH CHECK (
    tenant_id = futuranext.tenant() AND futuranext.has_permission('admin.entitlement.manage')
);
CREATE POLICY license_update ON futuranext.tenant_products FOR UPDATE TO fn_app USING (
    tenant_id = futuranext.tenant() AND futuranext.has_permission('admin.entitlement.manage')
) WITH CHECK (tenant_id = futuranext.tenant() AND futuranext.has_permission('admin.entitlement.manage'));

CREATE POLICY invitation_read ON futuranext.invitations FOR SELECT TO fn_app USING (
    futuranext.can_read_members(tenant_id)
    OR (
        futuranext.tenant() IS NULL AND accepted_at IS NULL AND expires_at > now()
        AND email = futuranext.confirmed_email() AND futuranext.tenant_active(tenant_id)
    )
);
CREATE POLICY invitation_create ON futuranext.invitations FOR INSERT TO fn_app WITH CHECK (
    futuranext.can_manage_member(tenant_id, role) AND accepted_at IS NULL
);
CREATE POLICY invitation_update ON futuranext.invitations FOR UPDATE TO fn_app USING (
    futuranext.can_manage_member(tenant_id, role) AND accepted_at IS NULL
) WITH CHECK (futuranext.can_manage_member(tenant_id, role) AND accepted_at IS NULL);

CREATE POLICY audit_read ON futuranext.audit_logs FOR SELECT TO fn_app
    USING (futuranext.tenant() IS NULL AND futuranext.has_permission('admin.audit.read'));

CREATE FUNCTION futuranext.pending_invitations(verified_email text)
RETURNS TABLE (id uuid, organization_name text, role text, expires_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = ''
AS $$
    SELECT invite.id, org.name, invite.role, invite.expires_at
    FROM futuranext.invitations AS invite JOIN futuranext.organizations AS org ON org.id = invite.tenant_id
    WHERE futuranext.tenant() IS NULL AND org.active AND invite.expires_at > now() AND invite.accepted_at IS NULL
        AND invite.email = futuranext.confirmed_email() AND invite.email = lower(btrim(verified_email))
    ORDER BY invite.created_at
$$;

CREATE FUNCTION futuranext.accept_invitation(invite_id uuid, verified_email text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = ''
AS $$
DECLARE
    invite futuranext.invitations%ROWTYPE;
    subject uuid := futuranext.actor();
    email_address text := futuranext.confirmed_email();
BEGIN
    IF futuranext.tenant() IS NOT NULL OR email_address IS NULL
        OR email_address IS DISTINCT FROM lower(btrim(verified_email)) THEN
        RETURN false;
    END IF;
    SELECT candidate.* INTO invite
    FROM futuranext.invitations AS candidate
    JOIN futuranext.organizations AS org ON org.id = candidate.tenant_id
    WHERE candidate.id = invite_id AND candidate.email = email_address AND org.active
    FOR UPDATE OF candidate;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    IF invite.accepted_at IS NOT NULL THEN
        RETURN invite.accepted_by = subject;
    END IF;
    IF invite.expires_at <= now() THEN
        RETURN false;
    END IF;
    IF EXISTS (
        SELECT FROM futuranext.organization_memberships
        WHERE tenant_id = invite.tenant_id AND user_id = subject AND NOT active
    ) THEN
        RETURN false;
    END IF;

    INSERT INTO futuranext.profiles (user_id, email) VALUES (subject, email_address)
    ON CONFLICT (user_id) DO UPDATE SET email = excluded.email;
    INSERT INTO futuranext.organization_memberships (tenant_id, user_id, role)
    VALUES (invite.tenant_id, subject, invite.role)
    ON CONFLICT (tenant_id, user_id) DO NOTHING;
    UPDATE futuranext.invitations SET accepted_at = now(), accepted_by = subject WHERE id = invite.id;
    RETURN true;
END
$$;

CREATE FUNCTION futuranext.audit_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = ''
AS $$
DECLARE
    target jsonb := to_jsonb(COALESCE(NEW, OLD));
BEGIN
    IF TG_OP = 'UPDATE' AND NEW IS NOT DISTINCT FROM OLD THEN
        RETURN NULL;
    END IF;
    INSERT INTO futuranext.audit_logs (actor_id, tenant_id, resource, operation)
    VALUES (
        futuranext.actor(),
        CASE WHEN TG_TABLE_NAME = 'organizations' THEN (target ->> 'id')::uuid
            ELSE (target ->> 'tenant_id')::uuid END,
        TG_TABLE_NAME, TG_OP
    );
    RETURN NULL;
END
$$;

DO $$
DECLARE
    relation text;
BEGIN
    FOREACH relation IN ARRAY ARRAY[
        'organizations', 'platform_roles', 'organization_memberships', 'support_assignments',
        'products', 'tenant_products', 'user_permissions', 'invitations'
    ] LOOP
        EXECUTE format(
            'CREATE TRIGGER audit_change AFTER INSERT OR UPDATE OR DELETE ON futuranext.%I
             FOR EACH ROW EXECUTE FUNCTION futuranext.audit_change()', relation
        );
    END LOOP;
END
$$;

REVOKE ALL ON ALL TABLES IN SCHEMA futuranext FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA futuranext FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
    futuranext.actor(), futuranext.tenant(), futuranext.has_mfa(), futuranext.platform_role(),
    futuranext.member_role(uuid), futuranext.tenant_active(uuid), futuranext.assigned_support(uuid),
    futuranext.confirmed_email(), futuranext.has_permission(text), futuranext.can_read_members(uuid),
    futuranext.can_manage_member(uuid, text), futuranext.can_view_product(uuid, uuid),
    futuranext.pending_invitations(text), futuranext.accept_invitation(uuid, text)
TO fn_app;

GRANT SELECT, INSERT ON futuranext.organizations, futuranext.products, futuranext.tenant_products,
    futuranext.invitations TO fn_app;
GRANT UPDATE (name, active) ON futuranext.organizations TO fn_app;
GRANT UPDATE (name, description, active) ON futuranext.products TO fn_app;
GRANT UPDATE (active, starts_at, ends_at) ON futuranext.tenant_products TO fn_app;
GRANT UPDATE (delivery, expires_at, delivery_claim_until) ON futuranext.invitations TO fn_app;
GRANT SELECT ON futuranext.organization_memberships, futuranext.profiles, futuranext.platform_roles,
    futuranext.support_assignments, futuranext.audit_logs TO fn_app;
GRANT UPDATE (role, active) ON futuranext.organization_memberships TO fn_app;
