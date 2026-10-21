"""Add encrypted semantic vectors and answer generator metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261021_0007"
down_revision: str | Sequence[str] | None = "20261021_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("document_chunks") as batch:
        batch.add_column(sa.Column("embedding", sa.Text(), nullable=True))
        batch.add_column(sa.Column("embedding_model", sa.String(length=160), nullable=True))
    with op.batch_alter_table("knowledge_answers") as batch:
        batch.add_column(
            sa.Column(
                "generator",
                sa.String(length=32),
                server_default="local",
                nullable=False,
            )
        )
        batch.add_column(sa.Column("model_name", sa.String(length=160), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("knowledge_answers") as batch:
        batch.drop_column("model_name")
        batch.drop_column("generator")
    with op.batch_alter_table("document_chunks") as batch:
        batch.drop_column("embedding_model")
        batch.drop_column("embedding")
