"""Encrypted PostgreSQL backup and isolated recovery-drill tooling.

This module is deliberately a CLI rather than an HTTP endpoint. Backup credentials and
restore authority must remain outside the application request path.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

import boto3
from botocore.config import Config
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from enterprise_ai_api.database import normalize_database_url
from enterprise_ai_api.object_storage import LocalObjectStorage, ObjectStorage, S3ObjectStorage

BACKUP_MAGIC: Final = b"EAKB1"
BACKUP_CONTEXT: Final = b"enterprise-ai:postgres-backup:v1"
MINIMUM_BACKUP_RETENTION_DAYS: Final = 35
MINIMUM_AUDIT_RETENTION_DAYS: Final = 365


class BackupError(RuntimeError):
    """Raised when backup configuration, creation, or verification fails."""


@dataclass(frozen=True)
class RetentionPolicy:
    backup_days: int
    audit_days: int
    minimum_recovery_points: int


@dataclass(frozen=True)
class BackupManifest:
    format_version: int
    backup_id: str
    created_at: str
    delete_after: str
    encrypted_object_key: str
    manifest_object_key: str
    encrypted_sha256: str
    encrypted_bytes: int
    source_database: str
    audit_retention_days: int

    def to_json(self) -> bytes:
        value = json.dumps(asdict(self), ensure_ascii=False, indent=2, sort_keys=True)
        return f"{value}\n".encode()

    @classmethod
    def from_json(cls, payload: bytes) -> BackupManifest:
        try:
            values = json.loads(payload)
            return cls(**values)
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise BackupError("Backup manifest is invalid") from error


def retention_policy_from_environment() -> RetentionPolicy:
    policy = RetentionPolicy(
        backup_days=_positive_integer("BACKUP_RETENTION_DAYS", 35),
        audit_days=_positive_integer("AUDIT_RETENTION_DAYS", 2555),
        minimum_recovery_points=_positive_integer("BACKUP_MIN_RECOVERY_POINTS", 7),
    )
    if policy.backup_days < MINIMUM_BACKUP_RETENTION_DAYS:
        raise BackupError(
            f"BACKUP_RETENTION_DAYS must be at least {MINIMUM_BACKUP_RETENTION_DAYS}"
        )
    if policy.audit_days < MINIMUM_AUDIT_RETENTION_DAYS:
        raise BackupError(f"AUDIT_RETENTION_DAYS must be at least {MINIMUM_AUDIT_RETENTION_DAYS}")
    return policy


def _positive_integer(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as error:
        raise BackupError(f"{name} must be an integer") from error
    if value <= 0:
        raise BackupError(f"{name} must be positive")
    return value


def _encryption_key() -> bytes:
    encoded = os.getenv("BACKUP_ENCRYPTION_KEY", "")
    if not encoded:
        raise BackupError("BACKUP_ENCRYPTION_KEY is required")
    try:
        key = base64.urlsafe_b64decode(encoded.encode())
    except (ValueError, TypeError) as error:
        raise BackupError("BACKUP_ENCRYPTION_KEY must be URL-safe base64") from error
    if len(key) != 32:
        raise BackupError("BACKUP_ENCRYPTION_KEY must decode to exactly 32 bytes")
    return key


def encrypt_backup(payload: bytes) -> bytes:
    nonce = os.urandom(12)
    return BACKUP_MAGIC + nonce + AESGCM(_encryption_key()).encrypt(
        nonce, payload, BACKUP_CONTEXT
    )


def decrypt_backup(payload: bytes) -> bytes:
    header_size = len(BACKUP_MAGIC)
    if not payload.startswith(BACKUP_MAGIC) or len(payload) < header_size + 13:
        raise BackupError("Encrypted backup header is invalid")
    nonce = payload[header_size : header_size + 12]
    try:
        return AESGCM(_encryption_key()).decrypt(
            nonce, payload[header_size + 12 :], BACKUP_CONTEXT
        )
    except Exception as error:
        raise BackupError("Backup decryption or authentication failed") from error


def backup_storage_from_environment() -> ObjectStorage:
    backend = os.getenv("BACKUP_STORAGE_BACKEND", "local").strip().lower()
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    if backend == "local":
        if app_env not in {"development", "test", "testing"}:
            raise BackupError("Production backups require BACKUP_STORAGE_BACKEND=s3")
        root = Path(os.getenv("BACKUP_STORAGE_PATH", "data/local/backups"))
        return LocalObjectStorage(root)
    if backend != "s3":
        raise BackupError("BACKUP_STORAGE_BACKEND must be either 'local' or 's3'")

    required_names = (
        "BACKUP_STORAGE_ACCESS_KEY",
        "BACKUP_STORAGE_SECRET_KEY",
        "BACKUP_STORAGE_BUCKET",
    )
    values = {name: os.getenv(name) for name in required_names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise BackupError(f"Missing backup storage settings: {', '.join(missing)}")

    force_path_style = os.getenv("BACKUP_STORAGE_FORCE_PATH_STYLE", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    client = boto3.client(
        "s3",
        endpoint_url=os.getenv("BACKUP_STORAGE_ENDPOINT") or None,
        aws_access_key_id=values["BACKUP_STORAGE_ACCESS_KEY"],
        aws_secret_access_key=values["BACKUP_STORAGE_SECRET_KEY"],
        region_name=os.getenv("BACKUP_STORAGE_REGION", "us-east-1"),
        config=Config(
            signature_version="s3v4",
            connect_timeout=5,
            read_timeout=60,
            retries={"mode": "standard", "max_attempts": 3},
            s3={"addressing_style": "path" if force_path_style else "auto"},
        ),
    )
    return S3ObjectStorage(
        bucket=values["BACKUP_STORAGE_BUCKET"] or "",
        client=client,
        prefix=os.getenv("BACKUP_STORAGE_PREFIX", "database-backups"),
        server_side_encryption=os.getenv("BACKUP_STORAGE_SSE") or None,
    )


def _postgres_url(value: str, *, setting_name: str) -> URL:
    try:
        url = make_url(normalize_database_url(value))
    except Exception as error:
        raise BackupError(f"{setting_name} is not a valid database URL") from error
    if url.get_backend_name() != "postgresql" or not url.database:
        raise BackupError(f"{setting_name} must point to PostgreSQL")
    return url


def _postgres_environment(url: URL) -> dict[str, str]:
    environment = os.environ.copy()
    mappings: dict[str, str | int | None] = {
        "PGHOST": url.host,
        "PGPORT": url.port,
        "PGUSER": url.username,
        "PGPASSWORD": url.password,
        "PGDATABASE": url.database,
    }
    for name, value in mappings.items():
        if value is not None:
            environment[name] = str(value)
    if sslmode := url.query.get("sslmode"):
        environment["PGSSLMODE"] = str(sslmode)
    return environment


def create_backup(
    *,
    database_url: str,
    storage: ObjectStorage | None = None,
    now: datetime | None = None,
) -> BackupManifest:
    source = _postgres_url(database_url, setting_name="DATABASE_URL")
    policy = retention_policy_from_environment()
    storage = storage or backup_storage_from_environment()
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    backup_id = f"{timestamp:%Y%m%dT%H%M%SZ}-{uuid4().hex[:12]}"
    object_prefix = f"postgres/{timestamp:%Y/%m/%d}/{backup_id}"
    encrypted_object_key = f"{object_prefix}.dump.enc"
    manifest_object_key = f"{object_prefix}.manifest.json"

    with tempfile.TemporaryDirectory(prefix="enterprise-ai-backup-") as directory:
        dump_path = Path(directory) / "database.dump"
        try:
            subprocess.run(
                [
                    "pg_dump",
                    "--format=custom",
                    "--no-owner",
                    "--no-privileges",
                    "--file",
                    str(dump_path),
                ],
                check=True,
                capture_output=True,
                env=_postgres_environment(source),
            )
        except FileNotFoundError as error:
            raise BackupError("pg_dump is not installed") from error
        except subprocess.CalledProcessError as error:
            message = error.stderr.decode(errors="replace").strip()
            raise BackupError(f"pg_dump failed: {message or 'unknown error'}") from error
        encrypted = encrypt_backup(dump_path.read_bytes())

    manifest = BackupManifest(
        format_version=1,
        backup_id=backup_id,
        created_at=timestamp.isoformat(),
        delete_after=(timestamp + timedelta(days=policy.backup_days)).isoformat(),
        encrypted_object_key=encrypted_object_key,
        manifest_object_key=manifest_object_key,
        encrypted_sha256=hashlib.sha256(encrypted).hexdigest(),
        encrypted_bytes=len(encrypted),
        source_database=source.database or "",
        audit_retention_days=policy.audit_days,
    )
    storage.put(encrypted_object_key, encrypted)
    try:
        storage.put(manifest_object_key, manifest.to_json())
    except Exception:
        storage.delete(encrypted_object_key)
        raise
    return manifest


def restore_drill(
    *,
    manifest_key: str,
    target_database_url: str,
    confirm_target: str,
    storage: ObjectStorage | None = None,
) -> dict[str, Any]:
    target = _postgres_url(target_database_url, setting_name="RESTORE_DATABASE_URL")
    target_name = target.database or ""
    if target_name != confirm_target:
        raise BackupError("--confirm-target must exactly match the target database name")
    if "_restore" not in target_name and "_drill" not in target_name:
        raise BackupError(
            "Recovery drills require a database name containing '_restore' or '_drill'"
        )

    production_url = os.getenv("DATABASE_URL")
    if production_url:
        source = _postgres_url(production_url, setting_name="DATABASE_URL")
        source_identity = (source.host, source.port, source.database)
        target_identity = (target.host, target.port, target.database)
        if source_identity == target_identity:
            raise BackupError("Recovery drill target cannot be the application database")

    storage = storage or backup_storage_from_environment()
    manifest = BackupManifest.from_json(storage.get(manifest_key))
    encrypted = storage.get(manifest.encrypted_object_key)
    if hashlib.sha256(encrypted).hexdigest() != manifest.encrypted_sha256:
        raise BackupError("Encrypted backup checksum does not match its manifest")
    if len(encrypted) != manifest.encrypted_bytes:
        raise BackupError("Encrypted backup size does not match its manifest")

    with tempfile.TemporaryDirectory(prefix="enterprise-ai-restore-") as directory:
        dump_path = Path(directory) / "database.dump"
        dump_path.write_bytes(decrypt_backup(encrypted))
        try:
            subprocess.run(
                [
                    "pg_restore",
                    "--clean",
                    "--if-exists",
                    "--no-owner",
                    "--no-privileges",
                    "--exit-on-error",
                    "--dbname",
                    target_name,
                    str(dump_path),
                ],
                check=True,
                capture_output=True,
                env=_postgres_environment(target),
            )
        except FileNotFoundError as error:
            raise BackupError("pg_restore is not installed") from error
        except subprocess.CalledProcessError as error:
            message = error.stderr.decode(errors="replace").strip()
            raise BackupError(f"pg_restore failed: {message or 'unknown error'}") from error

    engine = create_engine(normalize_database_url(target_database_url), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            required_tables = connection.execute(
                text(
                    "SELECT count(*) FROM (VALUES "
                    "(to_regclass('public.tenants')), "
                    "(to_regclass('public.users')), "
                    "(to_regclass('public.documents')), "
                    "(to_regclass('public.audit_events'))) AS required(table_name) "
                    "WHERE table_name IS NOT NULL"
                )
            ).scalar_one()
    finally:
        engine.dispose()
    if required_tables != 4:
        raise BackupError("Restored database is missing required tables")
    return {
        "status": "verified",
        "backup_id": manifest.backup_id,
        "target_database": target_name,
        "alembic_revision": revision,
        "required_tables": required_tables,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Encrypted PostgreSQL backup operations")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("policy", help="Print the active retention policy")
    subparsers.add_parser("create", help="Create and store an encrypted database backup")
    restore = subparsers.add_parser("restore-drill", help="Restore into an isolated drill database")
    restore.add_argument("--manifest-key", required=True)
    restore.add_argument("--target-url", default=os.getenv("RESTORE_DATABASE_URL"))
    restore.add_argument("--confirm-target", required=True)
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    if arguments.command == "policy":
        result: Any = asdict(retention_policy_from_environment())
    elif arguments.command == "create":
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise BackupError("DATABASE_URL is required")
        result = asdict(create_backup(database_url=database_url))
    else:
        if not arguments.target_url:
            raise BackupError("--target-url or RESTORE_DATABASE_URL is required")
        result = restore_drill(
            manifest_key=arguments.manifest_key,
            target_database_url=arguments.target_url,
            confirm_target=arguments.confirm_target,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
