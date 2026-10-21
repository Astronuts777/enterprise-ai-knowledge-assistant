"""Add privacy-preserving labels for account session management."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261021_0008"
down_revision: str | Sequence[str] | None = "20261021_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("auth_sessions") as batch:
        batch.add_column(
            sa.Column(
                "client_label",
                sa.String(length=120),
                server_default="Unknown browser",
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("auth_sessions") as batch:
        batch.drop_column("client_label")
