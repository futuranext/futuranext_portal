from datetime import date
from uuid import UUID

from sqlalchemy import text

from ..repository import Record, Repository


class BillingRepository(Repository):
    def _execute(self, query: str, **parameters) -> None:
        self.connection.execute(text(query), parameters)

    def account(self, tenant_id: UUID) -> Record | None:
        return self._one("""
            SELECT customer_id, environment FROM futuranext.billing_accounts WHERE tenant_id = :tenant
        """, tenant=tenant_id)

    def save_account(self, tenant_id: UUID, customer_id: str, environment: str) -> Record:
        self._scalar("""
            INSERT INTO futuranext.billing_customer_origins (tenant_id, customer_id, environment)
            VALUES (:tenant, :customer, :environment)
            ON CONFLICT (environment, customer_id) DO NOTHING RETURNING customer_id
        """, tenant=tenant_id, customer=customer_id, environment=environment)
        return self._rows("""
            INSERT INTO futuranext.billing_accounts (tenant_id, customer_id, environment)
            VALUES (:tenant, :customer, :environment)
            ON CONFLICT (tenant_id) DO UPDATE
                SET customer_id = excluded.customer_id, environment = excluded.environment
            RETURNING customer_id, environment
        """, tenant=tenant_id, customer=customer_id, environment=environment)[0]

    def try_lock_customer(self, tenant_id: UUID) -> bool:
        """Evita duas criações simultâneas do cliente Asaas da mesma empresa."""
        return bool(self._scalar(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))",
            key=f"futuranext:billing-customer:{tenant_id}",
        ))

    def customer_profile(self, tenant_id: UUID) -> Record | None:
        return self._one("""
            SELECT name, active, document, billing_email, billing_phone
            FROM futuranext.organizations WHERE id = :tenant
        """, tenant=tenant_id)

    def has_active_member(self, tenant_id: UUID) -> bool:
        return bool(self._scalar("""
            SELECT EXISTS (
                SELECT FROM futuranext.organization_memberships
                WHERE tenant_id = :tenant AND active
            )
        """, tenant=tenant_id))

    def link_customer_as_member(self, tenant_id: UUID, customer_id: str, environment: str) -> None:
        """Primeiro vínculo criado automaticamente durante o acesso do próprio cliente."""
        self._scalar("SELECT set_config('fn.billing_customer_sync', 'true', true)")
        self._execute("""
            INSERT INTO futuranext.billing_customer_origins (tenant_id, customer_id, environment)
            VALUES (:tenant, :customer, :environment)
            ON CONFLICT (environment, customer_id) DO NOTHING
        """, tenant=tenant_id, customer=customer_id, environment=environment)
        self._execute("""
            INSERT INTO futuranext.billing_accounts (tenant_id, customer_id, environment)
            VALUES (:tenant, :customer, :environment)
            ON CONFLICT (tenant_id) DO NOTHING
        """, tenant=tenant_id, customer=customer_id, environment=environment)
        self._scalar("SELECT set_config('fn.billing_customer_sync', '', true)")

    def services(self, tenant_id: UUID) -> list[Record]:
        return self._rows("""
            SELECT id, name, amount_cents, active FROM futuranext.billing_services
            WHERE tenant_id = :tenant ORDER BY active DESC, name, id
        """, tenant=tenant_id)

    def create_service(self, tenant_id: UUID, **values) -> Record:
        return self._rows("""
            INSERT INTO futuranext.billing_services (tenant_id, name, amount_cents, active)
            VALUES (:tenant, :name, :amount_cents, :active)
            RETURNING id, name, amount_cents, active
        """, tenant=tenant_id, **values)[0]

    def update_service(self, tenant_id: UUID, service_id: UUID, **values) -> Record | None:
        return self._one("""
            UPDATE futuranext.billing_services SET name = :name, amount_cents = :amount_cents,
                active = :active
            WHERE tenant_id = :tenant AND id = :id RETURNING id, name, amount_cents, active
        """, tenant=tenant_id, id=service_id, **values)

    def invoices(self, tenant_id: UUID) -> list[Record]:
        return self._rows("""
            SELECT * FROM futuranext.billing_invoices WHERE tenant_id = :tenant
            ORDER BY period DESC, created_at DESC, id LIMIT 120
        """, tenant=tenant_id)

    def locked_invoice(self, invoice_id: UUID, tenant_id: UUID | None) -> Record | None:
        return self._one("""
            SELECT * FROM futuranext.billing_invoices
            WHERE id = :id AND (CAST(:tenant AS uuid) IS NULL OR tenant_id = :tenant)
            FOR UPDATE
        """, id=invoice_id, tenant=tenant_id)

    def issue_invoice(
        self, tenant_id: UUID, period: date, due_date: date, environment: str,
    ) -> Record | None:
        return self._one("""
            WITH snapshot AS (
                SELECT jsonb_agg(jsonb_build_object('name', name, 'amount_cents', amount_cents)
                                 ORDER BY name, id) AS items,
                       sum(amount_cents) AS total
                FROM futuranext.billing_services WHERE tenant_id = :tenant AND active
                HAVING count(*) BETWEEN 1 AND 100
            )
            INSERT INTO futuranext.billing_invoices
                (tenant_id, period, due_date, total_cents, items, customer_id, environment)
            SELECT account.tenant_id, :period, :due, snapshot.total::integer, snapshot.items,
                account.customer_id, account.environment
            FROM snapshot CROSS JOIN futuranext.billing_accounts AS account
            WHERE account.tenant_id = :tenant AND account.environment = :environment
            RETURNING *
        """, tenant=tenant_id, period=period, due=due_date, environment=environment)

    def update_payment(
        self, invoice: Record, *, state: str, status: str, provider_id: str | None = None,
        url: str | None = None,
    ) -> Record:
        return self._rows("""
            UPDATE futuranext.billing_invoices
            SET provider_state = :state, status = :status, provider_id = :provider_id,
                payment_url = :url, updated_at = now()
            WHERE id = :id AND tenant_id = :tenant RETURNING *
        """, id=invoice["id"], tenant=invoice["tenant_id"], state=state, status=status,
            provider_id=provider_id, url=url)[0]

    def enable_portal_payment(self) -> None:
        self._scalar("SELECT set_config('fn.billing_payment', 'true', true)")

    def enable_webhook(self, environment: str) -> None:
        self._scalar(
            "SELECT set_config('fn.billing_webhook', :environment, true)", environment=environment,
        )

    def record_event(self, environment: str, event_id: str, invoice_id: UUID) -> bool:
        return self._scalar("""
            INSERT INTO futuranext.billing_events (environment, event_id, invoice_id)
            VALUES (:environment, :event, :invoice)
            ON CONFLICT (environment, event_id) DO NOTHING RETURNING event_id
        """, environment=environment, event=event_id, invoice=invoice_id) is not None

    # --- Assinatura mensal ------------------------------------------------------------------

    def enable_subscription_sync(self) -> None:
        """Permite ao administrador da empresa gravar a assinatura durante a compra de ferramentas."""
        self._scalar("SELECT set_config('fn.billing_subscription_sync', 'true', true)")

    def subscription(self, tenant_id: UUID) -> Record | None:
        return self._one("""
            SELECT tenant_id, environment, enabled, provider_id, provider_state, value_cents, next_due_date,
                updated_at
            FROM futuranext.billing_subscriptions WHERE tenant_id = :tenant
        """, tenant=tenant_id)

    def save_subscription(self, tenant_id: UUID, environment: str, **values) -> Record:
        """Cria ou atualiza a assinatura; só altera os campos informados."""
        current = self.subscription(tenant_id)
        merged = {
            "enabled": True, "provider_id": None, "provider_state": "unsubmitted",
            "value_cents": None, "next_due_date": None,
            **({key: current[key] for key in (
                "enabled", "provider_id", "provider_state", "value_cents", "next_due_date",
            )} if current else {}),
            **values,
        }
        return self._rows("""
            INSERT INTO futuranext.billing_subscriptions
                (tenant_id, environment, enabled, provider_id, provider_state, value_cents, next_due_date)
            VALUES (:tenant, :environment, :enabled, :provider_id, :provider_state, :value_cents, :next_due_date)
            ON CONFLICT (tenant_id) DO UPDATE SET environment = excluded.environment,
                enabled = excluded.enabled, provider_id = excluded.provider_id,
                provider_state = excluded.provider_state, value_cents = excluded.value_cents,
                next_due_date = excluded.next_due_date, updated_at = now()
            RETURNING tenant_id, environment, enabled, provider_id, provider_state, value_cents,
                next_due_date, updated_at
        """, tenant=tenant_id, environment=environment, **merged)[0]

    def try_lock_subscription(self, tenant_id: UUID) -> bool:
        return bool(self._scalar(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))",
            key=f"futuranext:billing-subscription:{tenant_id}",
        ))

    def active_total(self, tenant_id: UUID) -> int:
        """Soma dos serviços ativos; 0 quando não há serviço ou passa de 100 itens."""
        total = self._scalar("""
            SELECT CASE WHEN count(*) BETWEEN 1 AND 100 THEN sum(amount_cents) ELSE 0 END
            FROM futuranext.billing_services WHERE tenant_id = :tenant AND active
        """, tenant=tenant_id)
        return int(total or 0)

    def record_subscription_payment(
        self, environment: str, subscription_id: str, payment_id: str, due: date, amount_cents: int,
        status: str, url: str | None,
    ) -> UUID | None:
        return self._scalar("""
            SELECT futuranext.record_subscription_payment(
                :environment, :subscription, :payment, :due, :amount, :status, :url)
        """, environment=environment, subscription=subscription_id, payment=payment_id, due=due,
            amount=amount_cents, status=status, url=url)

    # --- Compra e teste grátis --------------------------------------------------------------

    def purchase_tool(self, tenant_id: UUID, product_id: UUID) -> bool:
        return bool(self._scalar(
            "SELECT futuranext.purchase_tool(:tenant, :product)", tenant=tenant_id, product=product_id,
        ))

    def trial(self, tenant_id: UUID) -> Record | None:
        return self._one(
            "SELECT started_at, ends_at FROM futuranext.trials WHERE tenant_id = :tenant", tenant=tenant_id,
        )
