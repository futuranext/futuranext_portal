"""Consultas do núcleo. Recebe a transação com identidade e tenant já verificados."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

Record = dict[str, Any]


class Repository:
    def __init__(self, connection: Connection):
        self.connection = connection

    def _rows(self, query: str, **parameters: Any) -> list[Record]:
        return [dict(row) for row in self.connection.execute(text(query), parameters).mappings()]

    def _one(self, query: str, **parameters: Any) -> Record | None:
        row = self.connection.execute(text(query), parameters).mappings().one_or_none()
        return dict(row) if row is not None else None

    def _scalar(self, query: str, **parameters: Any) -> Any:
        return self.connection.scalar(text(query), parameters)

    def has_permission(self, permission: str) -> bool:
        return bool(
            self._scalar("SELECT futuranext.has_permission(:permission)", permission=permission)
        )

    def can_use_tool(self, tenant_id: UUID, slug: str, *, write: bool = False) -> bool:
        return bool(self._scalar(
            "SELECT futuranext.can_use_tool(:tenant_id, :slug, :write)",
            tenant_id=tenant_id, slug=slug, write=write,
        ))

    def password_session_current(self, version: str | None) -> bool:
        return bool(self._scalar(
            "SELECT futuranext.password_session_current(:version)", version=version,
        ))

    def lock_password_account(self, email: str) -> bool:
        return bool(self._scalar(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(lower(:email), 8008))",
            email=email,
        ))

    def find_temporary_account(self, tenant_id: UUID, email: str) -> UUID | None:
        return self._scalar(
            "SELECT futuranext.find_temporary_account(:tenant_id, :email)",
            tenant_id=tenant_id, email=email,
        )

    def link_temporary_account(self, user_id: UUID, tenant_id: UUID, role: str) -> bool:
        return bool(self._scalar(
            "SELECT futuranext.link_temporary_account(:user_id, :tenant_id, :role)",
            user_id=user_id, tenant_id=tenant_id, role=role,
        ))

    def context(self) -> Record:
        return {
            "platform_role": self._scalar("SELECT futuranext.platform_role()"),
            "organizations": self._rows("""
                SELECT org.id, org.name, org.slug, membership.role
                FROM futuranext.organizations AS org
                JOIN futuranext.organization_memberships AS membership ON membership.tenant_id = org.id
                WHERE membership.user_id = futuranext.actor() AND org.active AND membership.active
                ORDER BY org.name, org.id
            """),
        }

    def tenant_products(self, tenant_id: UUID) -> list[Record]:
        return self._rows(
            """
            SELECT product.id, product.slug, product.name, product.description, product.launch_url
            FROM futuranext.products AS product
            JOIN futuranext.tenant_products AS license ON license.product_id = product.id
            WHERE license.tenant_id = :tenant_id
                AND futuranext.can_view_product(license.tenant_id, product.id)
                AND product.active AND license.active AND license.starts_at <= now()
                AND (license.ends_at IS NULL OR license.ends_at > now())
            ORDER BY product.name, product.id
        """,
            tenant_id=tenant_id,
        )

    def list_organizations(self) -> list[Record]:
        return self._rows("""
            SELECT id, name, slug, active
            FROM futuranext.organizations ORDER BY name, id
        """)

    def get_organization(self, tenant_id: UUID) -> Record | None:
        return self._one(
            """
            SELECT id, name, slug, active FROM futuranext.organizations WHERE id = :tenant_id
        """,
            tenant_id=tenant_id,
        )

    def create_organization(self, name: str, slug: str) -> Record | None:
        return self._one(
            """
            INSERT INTO futuranext.organizations (name, slug) VALUES (:name, :slug)
            RETURNING id, name, slug, active
        """,
            name=name,
            slug=slug,
        )

    def update_organization(self, tenant_id: UUID, name: str, active: bool) -> Record | None:
        return self._one(
            """
            UPDATE futuranext.organizations SET name = :name, active = :active WHERE id = :tenant_id
            RETURNING id, name, slug, active
        """,
            tenant_id=tenant_id,
            name=name,
            active=active,
        )

    def list_products(self) -> list[Record]:
        return self._rows("""
            SELECT id, slug, name, description, active, offerable, launch_url
            FROM futuranext.products ORDER BY name, id
        """)

    def create_product(
        self, slug: str, name: str, description: str, *,
        offerable: bool = False, launch_url: str | None = None,
    ) -> Record | None:
        return self._one(
            """
            INSERT INTO futuranext.products (slug, name, description, offerable, launch_url)
            VALUES (:slug, :name, :description, :offerable, :launch_url)
            RETURNING id, slug, name, description, active, offerable, launch_url
        """,
            slug=slug,
            name=name,
            description=description,
            offerable=offerable,
            launch_url=launch_url,
        )

    def update_product(
        self, product_id: UUID, name: str, description: str, active: bool, *,
        offerable: bool | None = None, launch_url: str | None = None, update_launch_url: bool = False,
    ) -> Record | None:
        return self._one(
            """
            UPDATE futuranext.products SET name = :name, description = :description, active = :active,
                offerable = COALESCE(:offerable, offerable),
                launch_url = CASE WHEN :update_launch_url THEN :launch_url ELSE launch_url END
            WHERE id = :product_id RETURNING id, slug, name, description, active, offerable, launch_url
        """,
            product_id=product_id,
            name=name,
            description=description,
            active=active,
            offerable=offerable,
            launch_url=launch_url,
            update_launch_url=update_launch_url,
        )

    def search_accounts(self, search: str) -> list[Record]:
        return self._rows("SELECT * FROM futuranext.search_accounts(:search)", search=search)

    def account_access(self, user_id: UUID) -> list[Record]:
        return self._rows("SELECT * FROM futuranext.account_access(:user_id)", user_id=user_id)

    def set_account_access(self, user_id: UUID, tenant_id: UUID, role: str, granted: bool) -> bool:
        return bool(self._scalar(
            "SELECT futuranext.set_account_access(:user_id, :tenant_id, :role, :granted)",
            user_id=user_id, tenant_id=tenant_id, role=role, granted=granted,
        ))

    def tool_offers(self, tenant_id: UUID) -> list[Record]:
        return self._rows("SELECT * FROM futuranext.tool_offers(:tenant_id)", tenant_id=tenant_id)

    def request_tool(self, tenant_id: UUID, product_id: UUID) -> UUID | None:
        return self._scalar(
            "SELECT futuranext.request_tool(:tenant_id, :product_id)",
            tenant_id=tenant_id, product_id=product_id,
        )

    def list_tool_requests(self) -> list[Record]:
        return self._rows("""
            SELECT req.id, org.name AS organization, product.name AS product, req.status, req.created_at
            FROM futuranext.tool_requests AS req
            JOIN futuranext.organizations AS org ON org.id = req.tenant_id
            JOIN futuranext.products AS product ON product.id = req.product_id
            ORDER BY (req.status = 'pending') DESC, req.created_at DESC, req.id
            LIMIT 200
        """)

    def review_tool_request(self, request_id: UUID, status: str) -> bool:
        return bool(self._scalar(
            "SELECT futuranext.review_tool_request(:request_id, :status)",
            request_id=request_id, status=status,
        ))

    def list_entitlements(self, tenant_id: UUID) -> list[Record]:
        return self._rows(
            """
            SELECT product_id, active, starts_at, ends_at FROM futuranext.tenant_products
            WHERE tenant_id = :tenant_id ORDER BY product_id
        """,
            tenant_id=tenant_id,
        )

    def upsert_entitlement(
        self,
        tenant_id: UUID,
        product_id: UUID,
        active: bool,
        starts_at: datetime | None,
        ends_at: datetime | None,
    ) -> Record | None:
        return self._one(
            """
            INSERT INTO futuranext.tenant_products (tenant_id, product_id, active, starts_at, ends_at)
            VALUES (:tenant_id, :product_id, :active, COALESCE(:starts_at, now()), :ends_at)
            ON CONFLICT (tenant_id, product_id) DO UPDATE SET
                active = excluded.active, starts_at = excluded.starts_at, ends_at = excluded.ends_at
            RETURNING product_id, active, starts_at, ends_at
        """,
            tenant_id=tenant_id,
            product_id=product_id,
            active=active,
            starts_at=starts_at,
            ends_at=ends_at,
        )

    def list_memberships(self, tenant_id: UUID) -> list[Record]:
        return self._rows(
            """
            SELECT member.user_id, profile.email, member.role, member.active
            FROM futuranext.organization_memberships AS member
            JOIN futuranext.profiles AS profile USING (user_id)
            WHERE member.tenant_id = :tenant_id ORDER BY profile.email, member.user_id
        """,
            tenant_id=tenant_id,
        )

    def update_membership(
        self, tenant_id: UUID, user_id: UUID, role: str, active: bool
    ) -> Record | None:
        return self._one(
            """
            WITH changed AS (
                UPDATE futuranext.organization_memberships AS membership SET role = :role, active = :active
                WHERE membership.tenant_id = :tenant_id AND membership.user_id = :user_id
                RETURNING membership.user_id, membership.role, membership.active
            )
            SELECT changed.user_id, profile.email, changed.role, changed.active
            FROM changed JOIN futuranext.profiles AS profile USING (user_id)
        """,
            tenant_id=tenant_id,
            user_id=user_id,
            role=role,
            active=active,
        )

    def list_invitations(self, tenant_id: UUID) -> list[Record]:
        return self._rows(
            """
            SELECT id, email, role, expires_at, accepted_at, delivery FROM futuranext.invitations
            WHERE tenant_id = :tenant_id ORDER BY created_at DESC, id
        """,
            tenant_id=tenant_id,
        )

    def create_invitation(self, tenant_id: UUID, email: str, role: str) -> Record | None:
        return self._one(
            """
            INSERT INTO futuranext.invitations AS current_invite (tenant_id, email, role)
            VALUES (:tenant_id, lower(btrim(:email)), :role)
            ON CONFLICT (tenant_id, email) WHERE accepted_at IS NULL DO UPDATE SET
                expires_at = CASE WHEN current_invite.expires_at <= now()
                    THEN excluded.expires_at ELSE current_invite.expires_at END,
                delivery = CASE WHEN current_invite.expires_at <= now()
                    THEN 'pending' ELSE current_invite.delivery END
            RETURNING id, email, role, expires_at, accepted_at, delivery
        """,
            tenant_id=tenant_id,
            email=email,
            role=role,
        )

    def claim_invitation_delivery(self, invitation_id: UUID) -> bool:
        return (
            self._scalar(
                """
            UPDATE futuranext.invitations SET
                delivery_claim_until = now() + interval '60 seconds', last_send_attempt_at = now()
            WHERE id = :invitation_id AND delivery IN ('pending', 'sent')
                AND accepted_at IS NULL AND expires_at > now()
                AND (delivery_claim_until IS NULL OR delivery_claim_until <= now())
                AND (last_send_attempt_at IS NULL OR last_send_attempt_at <= now() - interval '60 seconds')
            RETURNING id
        """,
                invitation_id=invitation_id,
            )
            is not None
        )

    def record_invitation_delivery(self, invitation_id: UUID, delivery: str) -> Record | None:
        return self._one(
            """
            UPDATE futuranext.invitations SET delivery = :delivery, delivery_claim_until = NULL
            WHERE id = :invitation_id AND delivery IN ('pending', 'sent') AND accepted_at IS NULL
                AND delivery_claim_until > now()
            RETURNING id, email, role, expires_at, accepted_at, delivery
        """,
            invitation_id=invitation_id,
            delivery=delivery,
        )

    def pending_invitations(self, verified_email: str) -> list[Record]:
        return self._rows(
            """
            SELECT id, organization_name, role, expires_at
            FROM futuranext.pending_invitations(:verified_email)
        """,
            verified_email=verified_email,
        )

    def accept_invitation(self, invitation_id: UUID, verified_email: str) -> bool:
        return bool(
            self._scalar(
                "SELECT futuranext.accept_invitation(:invitation_id, :verified_email)",
                invitation_id=invitation_id,
                verified_email=verified_email,
            )
        )

    def list_audit(self) -> list[Record]:
        return self._rows("""
            SELECT id, occurred_at, actor_id, tenant_id, resource, operation
            FROM futuranext.audit_logs ORDER BY occurred_at DESC, id LIMIT 100
        """)
