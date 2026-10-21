"""Add verified account recovery and space-level access control."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261021_0005"
down_revision: str | Sequence[str] | None = "20261021_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_POLICY = "nullif(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("email_verified_at", sa.DateTime(timezone=True)))

    op.execute(
        "UPDATE users SET email_verified_at = CURRENT_TIMESTAMP "
        "WHERE email_ciphertext IS NOT NULL"
    )

    op.create_table(
        "account_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "purpose IN ('email_verification', 'password_reset')",
            name="ck_account_tokens_purpose",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_account_tokens_user_id", "account_tokens", ["user_id"])
    op.create_index("ix_account_tokens_purpose", "account_tokens", ["purpose"])
    op.create_index("ix_account_tokens_expires_at", "account_tokens", ["expires_at"])

    op.create_table(
        "auth_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("identity_hash", sa.String(length=64), nullable=False),
        sa.Column("network_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_auth_attempts_identity_hash", "auth_attempts", ["identity_hash"])
    op.create_index("ix_auth_attempts_network_hash", "auth_attempts", ["network_hash"])
    op.create_index("ix_auth_attempts_attempted_at", "auth_attempts", ["attempted_at"])

    with op.batch_alter_table("spaces") as batch:
        batch.create_unique_constraint("uq_spaces_tenant_id_id", ["tenant_id", "id"])

    op.create_table(
        "space_memberships",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('manager', 'viewer')", name="ck_space_memberships_role"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "space_id"],
            ["spaces.tenant_id", "spaces.id"],
            ondelete="CASCADE",
            name="fk_space_memberships_space",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id", "space_id", "user_id"),
    )
    op.create_index("ix_space_memberships_space_id", "space_memberships", ["space_id"])
    op.create_index("ix_space_memberships_user_id", "space_memberships", ["user_id"])

    is_postgresql = op.get_bind().dialect.name == "postgresql"
    if is_postgresql:
        # Alembic runs without an application tenant context. Temporarily exempt the
        # two source tables so every existing tenant is included in the ACL backfill.
        op.execute('ALTER TABLE "spaces" NO FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "memberships" NO FORCE ROW LEVEL SECURITY')
    op.execute(
        "INSERT INTO space_memberships (tenant_id, space_id, user_id, role) "
        "SELECT spaces.tenant_id, spaces.id, memberships.user_id, "
        "CASE WHEN memberships.role IN ('enterprise_admin', 'knowledge_admin') "
        "THEN 'manager' ELSE 'viewer' END "
        "FROM spaces JOIN memberships ON memberships.tenant_id = spaces.tenant_id"
    )
    if is_postgresql:
        op.execute('ALTER TABLE "spaces" FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "memberships" FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "space_memberships" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "space_memberships" FORCE ROW LEVEL SECURITY')
        op.execute(
            'CREATE POLICY tenant_isolation ON "space_memberships" '
            f"USING (tenant_id = {TENANT_POLICY}) "
            f"WITH CHECK (tenant_id = {TENANT_POLICY})"
        )


def downgrade() -> None:
    op.drop_index("ix_space_memberships_user_id", table_name="space_memberships")
    op.drop_index("ix_space_memberships_space_id", table_name="space_memberships")
    op.drop_table("space_memberships")
    with op.batch_alter_table("spaces") as batch:
        batch.drop_constraint("uq_spaces_tenant_id_id", type_="unique")
    op.drop_index("ix_auth_attempts_attempted_at", table_name="auth_attempts")
    op.drop_index("ix_auth_attempts_network_hash", table_name="auth_attempts")
    op.drop_index("ix_auth_attempts_identity_hash", table_name="auth_attempts")
    op.drop_table("auth_attempts")
    op.drop_index("ix_account_tokens_expires_at", table_name="account_tokens")
    op.drop_index("ix_account_tokens_purpose", table_name="account_tokens")
    op.drop_index("ix_account_tokens_user_id", table_name="account_tokens")
    op.drop_table("account_tokens")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("email_verified_at")
