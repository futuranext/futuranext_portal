-- Registros anteriores podem referenciar contas já apagadas da plataforma.
-- A chave protege as novas gravações e remove os registros das contas excluídas daqui em diante.
CREATE INDEX audit_logs_by_actor ON futuranext.audit_logs (actor_id) WHERE actor_id IS NOT NULL;
ALTER TABLE futuranext.audit_logs ADD CONSTRAINT audit_logs_actor_fkey
    FOREIGN KEY (actor_id) REFERENCES auth.users(id) ON DELETE CASCADE NOT VALID;

CREATE OR REPLACE FUNCTION futuranext.search_accounts(search_term text)
RETURNS TABLE (user_id uuid, email text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
    SELECT account.id, account.email::text FROM auth.users account
    WHERE futuranext.tenant() IS NULL AND futuranext.has_permission('admin.tenant.manage')
      AND account.email IS NOT NULL
      AND position(lower(btrim(search_term)) in lower(account.email)) > 0
    ORDER BY lower(account.email), account.id LIMIT 50
$$;

CREATE FUNCTION futuranext.portal_user_deletion_email(subject uuid)
RETURNS text LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
    SELECT lower(btrim(account.email))
    FROM auth.users AS account
    WHERE account.id = subject AND account.id <> futuranext.actor()
      AND futuranext.tenant() IS NULL AND futuranext.has_mfa()
      AND futuranext.platform_role() = 'super_admin'
      AND NOT EXISTS (SELECT FROM futuranext.platform_roles WHERE user_id = subject)
$$;

CREATE FUNCTION futuranext.clean_deleted_user()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    profile_email text;
BEGIN
    -- A chave de plataforma usa o perfil. O bloqueio impede promoção durante a exclusão.
    SELECT profile.email INTO profile_email
    FROM futuranext.profiles AS profile
    WHERE profile.user_id = OLD.id FOR UPDATE;

    IF session_user = 'supabase_auth_admin' AND EXISTS (
        SELECT FROM futuranext.platform_roles WHERE user_id = OLD.id
    ) THEN
        RAISE EXCEPTION 'Uma conta interna da FuturaNext está protegida.' USING ERRCODE = '42501';
    END IF;

    DELETE FROM futuranext.invitations AS invitation
    WHERE invitation.accepted_by = OLD.id
       OR (
           invitation.accepted_by IS NULL
           AND invitation.email IN (lower(btrim(OLD.email)), profile_email)
           AND NOT EXISTS (
               SELECT FROM auth.users AS other
               WHERE other.id <> OLD.id AND lower(btrim(other.email)) = invitation.email
           )
       );
    RETURN OLD;
END
$$;

REVOKE ALL ON FUNCTION futuranext.portal_user_deletion_email(uuid),
    futuranext.clean_deleted_user() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION futuranext.portal_user_deletion_email(uuid) TO fn_app;

CREATE TRIGGER futuranext_clean_deleted_user BEFORE DELETE ON auth.users
    FOR EACH ROW EXECUTE FUNCTION futuranext.clean_deleted_user();
