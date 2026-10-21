"""Add password accounts, server-side sessions, and tenant invitations."""

import base64
import hashlib
import hmac
import os
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

revision: str = "20261021_0004"
down_revision: str | Sequence[str] | None = "20261021_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_POLICY = "nullif(current_setting('app.tenant_id', true), '')::uuid"
ENCRYPTED_PREFIX = "enc:v1:"
FILE_MAGIC = b"EAKA1"
SENSITIVE_COLUMNS = (
    ("document_chunks", "text"),
    ("knowledge_answers", "question"),
    ("knowledge_answers", "answer"),
    ("answer_citations", "excerpt"),
)


def _derive_key(purpose: bytes) -> bytes:
    secret = os.getenv(
        "ENCRYPTION_MASTER_KEY", "local-development-only-do-not-use-in-production"
    ).encode()
    return hmac.new(secret, b"enterprise-ai:" + purpose, hashlib.sha256).digest()


def encrypt_bytes(value: bytes, *, context: bytes) -> bytes:
    nonce = os.urandom(12)
    ciphertext = AESGCM(_derive_key(b"aes-256-gcm")).encrypt(nonce, value, context)
    return FILE_MAGIC + nonce + ciphertext


def decrypt_bytes(value: bytes, *, context: bytes) -> bytes:
    offset = len(FILE_MAGIC)
    nonce = value[offset : offset + 12]
    return AESGCM(_derive_key(b"aes-256-gcm")).decrypt(nonce, value[offset + 12 :], context)


def encrypt_text(value: str) -> str:
    encrypted = encrypt_bytes(value.encode(), context=b"database-field:v1")
    return ENCRYPTED_PREFIX + base64.urlsafe_b64encode(encrypted).decode()


def decrypt_text(value: str) -> str:
    payload = base64.urlsafe_b64decode(value.removeprefix(ENCRYPTED_PREFIX))
    return decrypt_bytes(payload, context=b"database-field:v1").decode()


def content_fingerprint(value: bytes) -> str:
    return hmac.new(_derive_key(b"content-fingerprint"), value, hashlib.sha256).hexdigest()


def _transform_sensitive_fields(*, encrypt: bool) -> None:
    connection = op.get_bind()
    is_postgresql = connection.dialect.name == "postgresql"
    tables = {table for table, _column in SENSITIVE_COLUMNS}
    if is_postgresql:
        for table in tables:
            op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')

    transform = encrypt_text if encrypt else decrypt_text
    for table, column in SENSITIVE_COLUMNS:
        rows = connection.execute(sa.text(f'SELECT id, "{column}" FROM "{table}"'))
        for row_id, value in rows:
            is_encrypted = isinstance(value, str) and value.startswith(ENCRYPTED_PREFIX)
            if value is None or (encrypt and is_encrypted) or (not encrypt and not is_encrypted):
                continue
            connection.execute(
                sa.text(
                    f'UPDATE "{table}" SET "{column}" = :value WHERE id = :row_id'
                ),
                {"value": transform(value), "row_id": row_id},
            )

    if is_postgresql:
        for table in tables:
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')


def _canonical_uuid(value: object) -> str:
    return str(UUID(str(value)))


def _transform_uploaded_files(*, encrypt: bool) -> None:
    connection = op.get_bind()
    is_postgresql = connection.dialect.name == "postgresql"
    if is_postgresql:
        op.execute('ALTER TABLE "document_versions" NO FORCE ROW LEVEL SECURITY')

    storage_root = Path(os.getenv("LOCAL_STORAGE_PATH", "data/local/uploads")).resolve()
    rows = connection.execute(
        sa.text(
            "SELECT id, tenant_id, document_id, filename, object_key "
            "FROM document_versions"
        )
    )
    for version_id, tenant_id, document_id, filename, object_key in rows:
        source = storage_root / object_key
        if not source.is_file():
            continue
        content = source.read_bytes()
        tenant_value = _canonical_uuid(tenant_id)
        document_value = _canonical_uuid(document_id)
        version_value = _canonical_uuid(version_id)
        context = f"{tenant_value}:{document_value}:{version_value}".encode()
        is_encrypted = content.startswith(FILE_MAGIC)
        if encrypt and not is_encrypted:
            transformed = encrypt_bytes(content, context=context)
            new_key = str(
                Path(tenant_value) / document_value / version_value / "payload.enc"
            )
            new_hash = content_fingerprint(content)
        elif not encrypt and is_encrypted:
            transformed = decrypt_bytes(content, context=context)
            new_key = str(Path(tenant_value) / document_value / version_value / filename)
            new_hash = hashlib.sha256(transformed).hexdigest()
        else:
            continue

        target = storage_root / new_key
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(transformed)
        temporary.replace(target)
        if source != target:
            source.unlink(missing_ok=True)
        connection.execute(
            sa.text(
                "UPDATE document_versions SET object_key = :object_key, "
                "content_hash = :content_hash WHERE id = :version_id"
            ),
            {
                "object_key": new_key,
                "content_hash": new_hash,
                "version_id": version_id,
            },
        )

    if is_postgresql:
        op.execute('ALTER TABLE "document_versions" FORCE ROW LEVEL SECURITY')


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("email_ciphertext", sa.Text(), nullable=True))
        batch.add_column(sa.Column("email_lookup_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("password_hash", sa.Text(), nullable=True))
        batch.add_column(sa.Column("default_tenant_id", sa.Uuid(), nullable=True))
        batch.create_unique_constraint("uq_users_email_lookup_hash", ["email_lookup_hash"])
        batch.create_foreign_key(
            "fk_users_default_tenant_id",
            "tenants",
            ["default_tenant_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])

    op.create_table(
        "invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("email_ciphertext", sa.Text(), nullable=False),
        sa.Column("email_lookup_hash", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("invited_by", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('enterprise_admin', 'knowledge_admin', 'member')",
            name="ck_invitations_role",
        ),
        sa.ForeignKeyConstraint(["invited_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_invitations_tenant_id", "invitations", ["tenant_id"])
    op.create_index("ix_invitations_email_lookup_hash", "invitations", ["email_lookup_hash"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute('ALTER TABLE "invitations" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "invitations" FORCE ROW LEVEL SECURITY')
        op.execute(
            'CREATE POLICY tenant_isolation ON "invitations" '
            f"USING (tenant_id = {TENANT_POLICY}) WITH CHECK (tenant_id = {TENANT_POLICY})"
        )

    _transform_sensitive_fields(encrypt=True)
    _transform_uploaded_files(encrypt=True)


def downgrade() -> None:
    _transform_uploaded_files(encrypt=False)
    _transform_sensitive_fields(encrypt=False)
    op.drop_index("ix_invitations_email_lookup_hash", table_name="invitations")
    op.drop_index("ix_invitations_tenant_id", table_name="invitations")
    op.drop_table("invitations")
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("fk_users_default_tenant_id", type_="foreignkey")
        batch.drop_constraint("uq_users_email_lookup_hash", type_="unique")
        batch.drop_column("default_tenant_id")
        batch.drop_column("password_hash")
        batch.drop_column("email_lookup_hash")
        batch.drop_column("email_ciphertext")
