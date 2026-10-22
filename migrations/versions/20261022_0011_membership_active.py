"""Add tenant-scoped member suspension without deleting accounts or space grants."""

import sqlalchemy as sa
from alembic import op

revision = "20261022_0011"
down_revision = "20261022_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memberships", sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true())
    )


def downgrade() -> None:
    op.drop_column("memberships", "active")
