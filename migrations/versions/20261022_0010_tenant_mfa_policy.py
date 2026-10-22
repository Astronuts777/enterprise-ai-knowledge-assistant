"""Add enforceable tenant MFA policy."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261022_0010"
down_revision: str | Sequence[str] | None = "20261021_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tenants") as batch:
        batch.add_column(
            sa.Column(
                "mfa_policy",
                sa.String(length=16),
                server_default="off",
                nullable=False,
            )
        )
        batch.create_check_constraint(
            "ck_tenants_mfa_policy",
            "mfa_policy IN ('off', 'admins', 'all')",
        )


def downgrade() -> None:
    with op.batch_alter_table("tenants") as batch:
        batch.drop_constraint("ck_tenants_mfa_policy", type_="check")
        batch.drop_column("mfa_policy")
