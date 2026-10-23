"""Opt-in, immutable-version publication approvals with encrypted comments."""

import sqlalchemy as sa
from alembic import op

revision = "20261023_0014"
down_revision = "20261022_0013"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "documents",
        sa.Column("approval_required", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "document_approvals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("version_id", sa.Uuid(), sa.ForeignKey("document_versions.id"), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("submitted_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("submission_note", sa.Text(), nullable=False),
        sa.Column("reviewed_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("review_note", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled', 'published')",
            name="ck_document_approval_status",
        ),
        sa.UniqueConstraint("version_id", "attempt", name="uq_document_approval_attempt"),
    )
    for column in ("tenant_id", "document_id", "version_id"):
        op.create_index(f"ix_document_approvals_{column}", "document_approvals", [column])
    op.create_index(
        "ix_document_approvals_inbox",
        "document_approvals",
        ["tenant_id", "status", "created_at", "id"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE document_approvals ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE document_approvals FORCE ROW LEVEL SECURITY")
        tenant = "nullif(current_setting('app.tenant_id', true), '')::uuid"
        op.execute(
            "CREATE POLICY tenant_isolation ON document_approvals "
            f"USING (tenant_id = {tenant}) WITH CHECK (tenant_id = {tenant})"
        )


def downgrade():
    raise RuntimeError("Restore a verified pre-upgrade backup to roll back this migration")
