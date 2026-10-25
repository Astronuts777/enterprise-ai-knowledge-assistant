"""Encrypted enterprise lobby and participant-only direct messages."""

import sqlalchemy as sa
from alembic import op

revision = "20261026_0015"
down_revision = "20261023_0014"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "chat_conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("conversation_key", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("participant_a", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("participant_b", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("last_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_id", "conversation_key", name="uq_chat_conversation_key"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_chat_conversation_tenant"),
        sa.CheckConstraint(
            "(kind = 'lobby' AND participant_a IS NULL AND participant_b IS NULL) OR "
            "(kind = 'direct' AND participant_a IS NOT NULL AND participant_b IS NOT NULL "
            "AND participant_a <> participant_b)",
            name="ck_chat_conversation_kind",
        ),
    )
    op.create_index("ix_chat_conversations_tenant_id", "chat_conversations", ["tenant_id"])
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("sender_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["chat_conversations.tenant_id", "chat_conversations.id"],
            name="fk_chat_message_conversation",
        ),
        sa.UniqueConstraint("conversation_id", "sequence", name="uq_chat_message_sequence"),
        sa.UniqueConstraint(
            "conversation_id", "sender_id", "client_id", name="uq_chat_message_retry"
        ),
        sa.CheckConstraint("sequence > 0", name="ck_chat_message_sequence"),
    )
    op.create_index(
        "ix_chat_messages_rate", "chat_messages", ["tenant_id", "sender_id", "created_at"]
    )
    if op.get_bind().dialect.name == "postgresql":
        tenant = "nullif(current_setting('app.tenant_id', true), '')::uuid"
        for table in ("chat_conversations", "chat_messages"):
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY tenant_isolation ON {table} "
                f"USING (tenant_id = {tenant}) WITH CHECK (tenant_id = {tenant})"
            )


def downgrade():
    raise RuntimeError("Restore a verified pre-upgrade backup to roll back this migration")
