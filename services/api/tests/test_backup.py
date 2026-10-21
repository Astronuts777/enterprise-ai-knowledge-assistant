import base64
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

import enterprise_ai_api.backup as backup_module
from enterprise_ai_api.backup import (
    BackupError,
    BackupManifest,
    create_backup,
    decrypt_backup,
    encrypt_backup,
    restore_drill,
    retention_policy_from_environment,
)
from enterprise_ai_api.object_storage import LocalObjectStorage


@pytest.fixture(autouse=True)
def backup_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    key = base64.urlsafe_b64encode(b"b" * 32).decode()
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", key)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)


def test_backup_encryption_is_authenticated() -> None:
    encrypted = encrypt_backup(b"private database dump")

    assert decrypt_backup(encrypted) == b"private database dump"
    tampered = encrypted[:-1] + bytes([encrypted[-1] ^ 1])
    with pytest.raises(BackupError, match="authentication failed"):
        decrypt_backup(tampered)


def test_retention_policy_rejects_short_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKUP_RETENTION_DAYS", "7")

    with pytest.raises(BackupError, match="at least 35"):
        retention_policy_from_environment()


def test_create_backup_writes_encrypted_dump_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = LocalObjectStorage(tmp_path / "backups")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        dump_path = Path(command[command.index("--file") + 1])
        dump_path.write_bytes(b"postgres custom dump")
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        assert environment["PGPASSWORD"] == "secret"
        assert "secret" not in command
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    manifest = create_backup(
        database_url="postgresql+psycopg://backup:secret@db:5432/enterprise_ai?sslmode=require",
        storage=storage,
        now=datetime(2026, 10, 21, 7, 0, tzinfo=UTC),
    )

    encrypted = storage.get(manifest.encrypted_object_key)
    stored_manifest = json.loads(storage.get(manifest.manifest_object_key))
    assert decrypt_backup(encrypted) == b"postgres custom dump"
    assert stored_manifest["encrypted_sha256"] == hashlib.sha256(encrypted).hexdigest()
    assert stored_manifest["delete_after"] == "2026-11-25T07:00:00+00:00"


def test_restore_drill_rejects_unsafe_target(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path)

    with pytest.raises(BackupError, match="_restore"):
        restore_drill(
            manifest_key="missing.json",
            target_database_url="postgresql+psycopg://owner:secret@db/enterprise_ai",
            confirm_target="enterprise_ai",
            storage=storage,
        )


def test_restore_drill_checks_confirmation_before_reading_storage(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path)

    with pytest.raises(BackupError, match="exactly match"):
        restore_drill(
            manifest_key="missing.json",
            target_database_url="postgresql+psycopg://owner:secret@db/enterprise_ai_restore",
            confirm_target="wrong_name",
            storage=storage,
        )


def test_restore_drill_verifies_checksum_runs_restore_and_checks_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = LocalObjectStorage(tmp_path)
    encrypted = encrypt_backup(b"postgres custom dump")
    manifest = BackupManifest(
        format_version=1,
        backup_id="20261021T070000Z-example",
        created_at="2026-10-21T07:00:00+00:00",
        delete_after="2026-11-25T07:00:00+00:00",
        encrypted_object_key="postgres/example.dump.enc",
        manifest_object_key="postgres/example.manifest.json",
        encrypted_sha256=hashlib.sha256(encrypted).hexdigest(),
        encrypted_bytes=len(encrypted),
        source_database="enterprise_ai",
        audit_retention_days=2555,
    )
    storage.put(manifest.encrypted_object_key, encrypted)
    storage.put(manifest.manifest_object_key, manifest.to_json())

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert command[0] == "pg_restore"
        assert "--clean" in command
        assert "enterprise_ai_drill" in command
        assert "secret" not in command
        assert Path(command[-1]).read_bytes() == b"postgres custom dump"
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        assert environment["PGPASSWORD"] == "secret"
        return subprocess.CompletedProcess(command, 0, b"", b"")

    class FakeResult:
        def __init__(self, value: str | int) -> None:
            self.value = value

        def scalar_one(self) -> str | int:
            return self.value

    class FakeConnection:
        def execute(self, statement: object) -> FakeResult:
            value: str | int = "20261021_0006" if "alembic_version" in str(statement) else 4
            return FakeResult(value)

        def __enter__(self) -> "FakeConnection":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class FakeEngine:
        def connect(self) -> FakeConnection:
            return FakeConnection()

        def dispose(self) -> None:
            return None

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(backup_module, "create_engine", lambda *_args, **_kwargs: FakeEngine())

    result = restore_drill(
        manifest_key=manifest.manifest_object_key,
        target_database_url=(
            "postgresql+psycopg://restore:secret@drill-db:5432/enterprise_ai_drill"
        ),
        confirm_target="enterprise_ai_drill",
        storage=storage,
    )

    assert result == {
        "status": "verified",
        "backup_id": "20261021T070000Z-example",
        "target_database": "enterprise_ai_drill",
        "alembic_revision": "20261021_0006",
        "required_tables": 4,
    }
