ALTER TABLE futuranext.products
    ADD COLUMN offerable boolean NOT NULL DEFAULT false,
    ADD COLUMN launch_url text CHECK (
        length(launch_url) <= 2048 AND launch_url LIKE 'https://%'
    );
GRANT UPDATE (offerable, launch_url) ON futuranext.products TO fn_app;

CREATE FUNCTION futuranext.can_use_tool(org uuid, slug_to_check text, require_write boolean DEFAULT false)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
    SELECT EXISTS (
        SELECT FROM futuranext.products product
        JOIN futuranext.tenant_products license ON license.product_id = product.id
        WHERE license.tenant_id = org AND product.slug = slug_to_check AND product.active
          AND license.active AND license.starts_at <= now()
          AND (license.ends_at IS NULL OR license.ends_at > now())
          AND futuranext.can_view_product(org, product.id)
          AND (NOT require_write OR futuranext.member_role(org) = 'cliente_admin')
    )
$$;

CREATE FUNCTION futuranext.search_accounts(search_term text)
RETURNS TABLE (user_id uuid, email text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
    SELECT account.id, account.email::text FROM auth.users account
    WHERE futuranext.tenant() IS NULL AND futuranext.has_permission('admin.tenant.manage')
      AND account.email_confirmed_at IS NOT NULL AND account.email IS NOT NULL
      AND position(lower(btrim(search_term)) in lower(account.email)) > 0
    ORDER BY lower(account.email), account.id LIMIT 50
$$;

CREATE FUNCTION futuranext.account_access(subject uuid)
RETURNS TABLE (tenant_id uuid, name text, company_active boolean, role text, granted boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
    SELECT org.id, org.name, org.active, coalesce(member.role, 'cliente_usuario'),
           coalesce(member.active, false)
    FROM futuranext.organizations org
    LEFT JOIN futuranext.organization_memberships member
      ON member.tenant_id = org.id AND member.user_id = subject
    WHERE futuranext.tenant() IS NULL AND futuranext.has_permission('admin.tenant.manage')
      AND EXISTS (SELECT FROM auth.users WHERE id = subject AND email_confirmed_at IS NOT NULL)
    ORDER BY org.name, org.id
$$;

CREATE FUNCTION futuranext.set_account_access(subject uuid, org uuid, target_role text, granted boolean)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE confirmed_address text;
BEGIN
    IF futuranext.tenant() IS NOT NULL OR NOT futuranext.has_permission('admin.tenant.manage')
       OR target_role NOT IN ('cliente_admin', 'cliente_usuario') OR granted IS NULL THEN
        RETURN false;
    END IF;
    IF NOT EXISTS (SELECT FROM futuranext.organizations WHERE id = org AND (active OR NOT granted)) THEN
        RETURN false;
    END IF;
    SELECT lower(btrim(email)) INTO confirmed_address FROM auth.users
      WHERE id = subject AND email_confirmed_at IS NOT NULL;
    IF confirmed_address IS NULL THEN RETURN false; END IF;
    IF NOT granted THEN
        DELETE FROM futuranext.invitations
          WHERE tenant_id = org AND email = confirmed_address AND accepted_at IS NULL;
        UPDATE futuranext.organization_memberships SET active = false
          WHERE tenant_id = org AND user_id = subject AND active;
        RETURN true;
    END IF;
    INSERT INTO futuranext.profiles (user_id, email) VALUES (subject, confirmed_address)
    ON CONFLICT (user_id) DO UPDATE SET email = excluded.email
      WHERE profiles.email IS DISTINCT FROM excluded.email;
    INSERT INTO futuranext.organization_memberships (tenant_id, user_id, role)
      VALUES (org, subject, target_role)
    ON CONFLICT (tenant_id, user_id) DO UPDATE SET role = excluded.role, active = true
      WHERE organization_memberships.role IS DISTINCT FROM excluded.role OR NOT organization_memberships.active;
    RETURN true;
END
$$;

CREATE TABLE futuranext.tool_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES futuranext.organizations,
    product_id uuid NOT NULL REFERENCES futuranext.products,
    requested_by uuid REFERENCES auth.users ON DELETE SET NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at timestamptz NOT NULL DEFAULT now(),
    decided_at timestamptz,
    decided_by uuid REFERENCES auth.users ON DELETE SET NULL
);
CREATE UNIQUE INDEX one_pending_tool_request ON futuranext.tool_requests (tenant_id, product_id)
WHERE status = 'pending';
CREATE INDEX tool_requests_review_queue ON futuranext.tool_requests (status, created_at DESC);
ALTER TABLE futuranext.tool_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE futuranext.tool_requests FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
    EXECUTE format('CREATE POLICY request_owner ON futuranext.tool_requests TO %I USING (true) WITH CHECK (true)', current_user);
END $$;
CREATE POLICY requests_visible ON futuranext.tool_requests FOR SELECT TO fn_app USING (
    (futuranext.tenant() IS NULL AND futuranext.has_permission('admin.entitlement.manage'))
    OR (tenant_id = futuranext.tenant() AND futuranext.has_permission('tenant.access'))
);
GRANT SELECT ON futuranext.tool_requests TO fn_app;
CREATE TRIGGER audit_change AFTER INSERT OR UPDATE OR DELETE ON futuranext.tool_requests
FOR EACH ROW EXECUTE FUNCTION futuranext.audit_change();

CREATE FUNCTION futuranext.tool_offers(org uuid)
RETURNS TABLE (id uuid, slug text, name text, description text, pending boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
    SELECT product.id, product.slug, product.name, product.description, EXISTS (
        SELECT FROM futuranext.tool_requests request WHERE request.tenant_id = org
          AND request.product_id = product.id AND request.status = 'pending'
    ) FROM futuranext.products product
    WHERE org = futuranext.tenant() AND futuranext.has_permission('tenant.access')
      AND product.active AND product.offerable AND NOT EXISTS (
        SELECT FROM futuranext.tenant_products license WHERE license.tenant_id = org
          AND license.product_id = product.id AND license.active AND license.starts_at <= now()
          AND (license.ends_at IS NULL OR license.ends_at > now())
      )
    ORDER BY product.name, product.id
$$;

CREATE FUNCTION futuranext.request_tool(org uuid, item uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE request_id uuid;
BEGIN
    IF org IS DISTINCT FROM futuranext.tenant() OR NOT futuranext.has_permission('tenant.access') THEN
        RETURN NULL;
    END IF;
    IF NOT EXISTS (SELECT FROM futuranext.tool_offers(org) WHERE id = item) THEN RETURN NULL; END IF;
    INSERT INTO futuranext.tool_requests (tenant_id, product_id, requested_by)
      VALUES (org, item, futuranext.actor())
      ON CONFLICT (tenant_id, product_id) WHERE status = 'pending' DO NOTHING
      RETURNING id INTO request_id;
    IF request_id IS NULL THEN
        SELECT id INTO request_id FROM futuranext.tool_requests
          WHERE tenant_id = org AND product_id = item AND status = 'pending';
    END IF;
    RETURN request_id;
END
$$;

CREATE FUNCTION futuranext.review_tool_request(request_id uuid, outcome text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE selected futuranext.tool_requests%ROWTYPE;
BEGIN
    IF futuranext.tenant() IS NOT NULL OR NOT futuranext.has_permission('admin.entitlement.manage')
       OR outcome NOT IN ('approved', 'rejected') THEN RETURN false; END IF;
    SELECT * INTO selected FROM futuranext.tool_requests WHERE id = request_id FOR UPDATE;
    IF NOT FOUND THEN RETURN false; END IF;
    IF selected.status <> 'pending' THEN RETURN selected.status = outcome; END IF;
    IF outcome = 'approved' THEN
        IF NOT (futuranext.tenant_active(selected.tenant_id) AND EXISTS (
            SELECT FROM futuranext.products WHERE id = selected.product_id AND active
        )) THEN RETURN false; END IF;
        INSERT INTO futuranext.tenant_products (tenant_id, product_id)
          VALUES (selected.tenant_id, selected.product_id)
        ON CONFLICT (tenant_id, product_id) DO UPDATE
          SET active = true, starts_at = now(), ends_at = NULL
          WHERE NOT tenant_products.active OR tenant_products.starts_at > now()
             OR tenant_products.ends_at <= now();
    END IF;
    UPDATE futuranext.tool_requests SET status = outcome, decided_at = now(), decided_by = futuranext.actor()
      WHERE id = request_id;
    RETURN true;
END
$$;

REVOKE ALL ON FUNCTION futuranext.can_use_tool(uuid, text, boolean), futuranext.search_accounts(text),
    futuranext.account_access(uuid), futuranext.set_account_access(uuid, uuid, text, boolean),
    futuranext.tool_offers(uuid), futuranext.request_tool(uuid, uuid), futuranext.review_tool_request(uuid, text)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION futuranext.can_use_tool(uuid, text, boolean), futuranext.search_accounts(text),
    futuranext.account_access(uuid), futuranext.set_account_access(uuid, uuid, text, boolean),
    futuranext.tool_offers(uuid), futuranext.request_tool(uuid, uuid), futuranext.review_tool_request(uuid, text)
TO fn_app;

INSERT INTO futuranext.products (slug, name, description, offerable) VALUES
    ('whatsapp', 'Conexão WhatsApp', 'Gerencie a integração de WhatsApp da empresa.', true),
    ('cotacao-seguros', 'Portal de cotações', 'Acesso ao portal de cotações da Equinos Seguros.', false),
    ('nfse-consulta', 'Consulta de NFS-e', 'Configure certificados e CNPJs para consulta de notas emitidas.', false)
ON CONFLICT (slug) DO NOTHING;
UPDATE futuranext.products SET offerable = true WHERE slug = 'nfse';

INSERT INTO futuranext.organizations (slug, name)
SELECT seed.slug, seed.name FROM (VALUES
    ('equinos-seguros', 'Equinos Seguros'), ('ccp', 'CCP - Clegio Clemente Pavan'),
    ('petcamp', 'PetCamp'), ('estetica-paraiso', 'Estetica Paraiso'), ('barbearia-stylus', 'Barbearia Stylus')
) AS seed(slug, name)
WHERE NOT EXISTS (SELECT FROM futuranext.organizations WHERE lower(btrim(name)) = lower(seed.name))
ON CONFLICT (slug) DO NOTHING;

INSERT INTO futuranext.tenant_products (tenant_id, product_id)
SELECT org.id, product.id FROM (VALUES
    ('Equinos Seguros', 'cotacao-seguros'), ('CCP - Clegio Clemente Pavan', 'nfse'),
    ('PetCamp', 'nfse-consulta'), ('Estetica Paraiso', 'clinica'), ('Barbearia Stylus', 'barbearia')
) AS seed(name, item)
JOIN futuranext.organizations org ON lower(btrim(org.name)) = lower(seed.name)
JOIN futuranext.products product ON product.slug = seed.item
WHERE (SELECT count(*) FROM futuranext.organizations WHERE lower(btrim(name)) = lower(seed.name)) = 1
ON CONFLICT (tenant_id, product_id) DO NOTHING;
