"""Per-enterprise owners and explicit document ACLs; never promote legacy accounts."""

import sqlalchemy as sa
from alembic import op

revision = "20261022_0013"
down_revision = "20261022_0012"
branch_labels = None
depends_on = None


def upgrade():
    # Nullable by design: existing tenants require independently verified onboarding.
    # SQLite uses application validation here to avoid rebuilding referenced tables.
    op.add_column("tenants", sa.Column("owner_user_id", sa.Uuid(), nullable=True))
    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key("fk_tenant_owner", "tenants", "users", ["owner_user_id"], ["id"])
    op.add_column("memberships", sa.Column("display_name", sa.Text(), nullable=True))
    op.add_column("memberships", sa.Column("department", sa.Text(), nullable=True))
    op.add_column(
        "documents", sa.Column("access_mode", sa.String(16), nullable=False, server_default="space")
    )
    if op.get_bind().dialect.name == "postgresql":
        op.create_check_constraint(
            "ck_document_access_mode", "documents", "access_mode IN ('space', 'restricted')"
        )
    op.create_table(
        "document_grants",
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("role IN ('reader', 'editor')", name="ck_document_grant_role"),
    )
    op.create_index("ix_document_grants_actor", "document_grants",
                    ["tenant_id", "user_id", "document_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE document_grants ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE document_grants FORCE ROW LEVEL SECURITY")
        policy = "nullif(current_setting('app.tenant_id', true), '')::uuid"
        op.execute(
            f"CREATE POLICY tenant_isolation ON document_grants "
            f"USING (tenant_id = {policy}) WITH CHECK (tenant_id = {policy})"
        )


def downgrade():
    raise RuntimeError("Restore a verified pre-upgrade backup to roll back this migration")
