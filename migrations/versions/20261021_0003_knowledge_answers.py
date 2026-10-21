"""Create evidence-grounded answer history and immutable citation snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261021_0003"
down_revision: str | Sequence[str] | None = "20261021_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_POLICY = "nullif(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "knowledge_answers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('grounded', 'insufficient_evidence')",
            name="ck_knowledge_answers_status",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_knowledge_answers_created_by"),
        "knowledge_answers",
        ["created_by"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_answers_tenant_id"),
        "knowledge_answers",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "answer_citations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("answer_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("document_chunk_id", sa.Uuid(), nullable=False),
        sa.Column("citation_number", sa.Integer(), nullable=False),
        sa.Column("document_title", sa.String(length=255), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("locator", sa.String(length=160), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("relevance_score", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["answer_id"], ["knowledge_answers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_chunk_id"], ["document_chunks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["document_version_id"], ["document_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("answer_id", "citation_number", name="uq_answer_citations_number"),
    )
    op.create_index(
        op.f("ix_answer_citations_answer_id"),
        "answer_citations",
        ["answer_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_answer_citations_tenant_id"),
        "answer_citations",
        ["tenant_id"],
        unique=False,
    )

    if op.get_bind().dialect.name == "postgresql":
        for table in ("knowledge_answers", "answer_citations"):
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(
                f'CREATE POLICY tenant_isolation ON "{table}" '
                f'USING (tenant_id = {TENANT_POLICY}) '
                f'WITH CHECK (tenant_id = {TENANT_POLICY})'
            )


def downgrade() -> None:
    op.drop_index(op.f("ix_answer_citations_tenant_id"), table_name="answer_citations")
    op.drop_index(op.f("ix_answer_citations_answer_id"), table_name="answer_citations")
    op.drop_table("answer_citations")
    op.drop_index(op.f("ix_knowledge_answers_tenant_id"), table_name="knowledge_answers")
    op.drop_index(op.f("ix_knowledge_answers_created_by"), table_name="knowledge_answers")
    op.drop_table("knowledge_answers")
