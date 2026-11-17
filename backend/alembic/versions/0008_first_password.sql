CREATE FUNCTION futuranext.password_session_current(expected_version text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
    SELECT EXISTS (
        SELECT FROM auth.users AS account
        WHERE account.id = futuranext.actor()
          AND account.raw_app_meta_data -> 'futuranext_first_password' IS DISTINCT FROM 'true'::jsonb
          AND account.raw_app_meta_data ->> 'futuranext_password_version'
              IS NOT DISTINCT FROM expected_version
    )
$$;

CREATE FUNCTION futuranext.find_temporary_account(org uuid, address text)
RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
    SELECT account.id
    FROM auth.users AS account
    WHERE futuranext.tenant() IS NULL AND futuranext.has_permission('admin.tenant.manage')
      AND lower(account.email) = lower(btrim(address))
      AND NOT EXISTS (SELECT FROM futuranext.platform_roles WHERE user_id = account.id)
      AND NOT EXISTS (SELECT FROM futuranext.organization_memberships WHERE user_id = account.id)
      AND (
        (
            account.invited_at IS NOT NULL
            AND EXISTS (
                SELECT FROM futuranext.invitations AS invite
                WHERE invite.tenant_id = org AND invite.email = lower(btrim(address))
                  AND invite.accepted_at IS NULL
                  AND (invite.delivery = 'sent' OR nullif(account.encrypted_password, '') IS NULL)
            )
        ) OR (
            account.last_sign_in_at IS NULL
            AND account.raw_app_meta_data -> 'futuranext_first_password' = 'true'::jsonb
            AND account.raw_app_meta_data ->> 'futuranext_password_company' = org::text
        )
      )
$$;

CREATE FUNCTION futuranext.link_temporary_account(subject uuid, org uuid, target_role text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE address text;
BEGIN
    IF futuranext.tenant() IS NOT NULL OR NOT futuranext.has_permission('admin.tenant.manage') THEN
        RETURN false;
    END IF;
    SELECT lower(account.email) INTO address FROM auth.users AS account
    WHERE id = subject AND account.email_confirmed_at IS NOT NULL
      AND account.raw_app_meta_data -> 'futuranext_first_password' = 'true'::jsonb
      AND account.raw_app_meta_data ->> 'futuranext_password_company' = org::text;
    IF address IS NULL OR NOT futuranext.set_account_access(subject, org, target_role, true) THEN
        RETURN false;
    END IF;
    DELETE FROM futuranext.invitations
    WHERE tenant_id = org AND email = address AND accepted_at IS NULL;
    RETURN true;
END
$$;

REVOKE ALL ON FUNCTION futuranext.password_session_current(text),
    futuranext.find_temporary_account(uuid, text), futuranext.link_temporary_account(uuid, uuid, text)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION futuranext.password_session_current(text),
    futuranext.find_temporary_account(uuid, text), futuranext.link_temporary_account(uuid, uuid, text)
TO fn_app;
