"""Durable ingestion queue and opt-in enterprise identity federation."""

import sqlalchemy as sa
from alembic import op

revision = "20261022_0012"
down_revision = "20261022_0011"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "auth_sessions",
        sa.Column("auth_method", sa.String(16), nullable=False, server_default="password"),
    )
    op.add_column(
        "memberships",
        sa.Column("provisioning_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    with op.batch_alter_table("document_versions") as batch:
        batch.drop_constraint("ck_document_versions_status", type_="check")
        batch.create_check_constraint(
            "ck_document_versions_status",
            "status IN ('queued', 'processing', 'ready', 'published', 'superseded', 'failed')",
        )
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "version_id",
            sa.Uuid(),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(64)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
    )
    op.create_table(
        "federated_identities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("issuer", sa.String(512), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("subject_hash", sa.String(64), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("tenant_id", "provider", "subject_hash"),
    )
    op.create_table(
        "federated_groups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("issuer", sa.String(512), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("external_id_hash", sa.String(64), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("members_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("tenant_id", "provider", "external_id_hash"),
    )
    op.create_table(
        "oidc_transactions",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("browser_hash", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("nonce", sa.String(64), nullable=False),
        sa.Column("verifier", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    for table in ("ingestion_jobs", "federated_identities", "federated_groups"):
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
        if op.get_bind().dialect.name == "postgresql":
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            policy = "nullif(current_setting('app.tenant_id', true), '')::uuid"
            op.execute(
                f'CREATE POLICY tenant_isolation ON "{table}" '
                f'USING (tenant_id = {policy}) WITH CHECK (tenant_id = {policy})'
            )


def downgrade():
    # Never silently destroy queued work or enterprise identity bindings.
    raise RuntimeError("Restore a verified pre-upgrade backup to roll back this migration")
