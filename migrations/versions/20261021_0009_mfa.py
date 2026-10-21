"""Add encrypted TOTP MFA, login challenges, and one-time recovery codes."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261021_0009"
down_revision: str | Sequence[str] | None = "20261021_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("mfa_secret_ciphertext", sa.Text()))
        batch.add_column(sa.Column("mfa_enabled_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("mfa_last_counter", sa.BigInteger()))

    with op.batch_alter_table("account_tokens") as batch:
        batch.add_column(
            sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False)
        )
        batch.drop_constraint("ck_account_tokens_purpose", type_="check")
        batch.create_check_constraint(
            "ck_account_tokens_purpose",
            "purpose IN ('email_verification', 'password_reset', 'mfa_login')",
        )

    op.create_table(
        "mfa_recovery_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash"),
    )
    op.create_index("ix_mfa_recovery_codes_user_id", "mfa_recovery_codes", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_mfa_recovery_codes_user_id", table_name="mfa_recovery_codes")
    op.drop_table("mfa_recovery_codes")
    with op.batch_alter_table("account_tokens") as batch:
        batch.drop_constraint("ck_account_tokens_purpose", type_="check")
        batch.create_check_constraint(
            "ck_account_tokens_purpose",
            "purpose IN ('email_verification', 'password_reset')",
        )
        batch.drop_column("attempt_count")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("mfa_last_counter")
        batch.drop_column("mfa_enabled_at")
        batch.drop_column("mfa_secret_ciphertext")
