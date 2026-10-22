import io
import json
import os
import sqlite3
import zipfile

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient
from test_documents_api import ADMIN_A, create_workspace, headers

from enterprise_ai_api import local_backup as backup
from enterprise_ai_api.main import create_app


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    source = tmp_path / "local-data"
    source.mkdir()
    secret = "test-application-key-" + "a" * 48
    (source / "master.key").write_text(secret)
    monkeypatch.setenv("ENCRYPTION_MASTER_KEY", secret)
    app = create_app(f"sqlite:///{source / 'enterprise.db'}", storage_root=str(source / "uploads"))
    with TestClient(app) as client:
        tenant, space = create_workspace(client, ADMIN_A, "Backup company")
        response = client.post(
            f"/v1/tenants/{tenant}/spaces/{space}/documents",
            files={"file": ("policy.txt", b"confidential backup policy")},
            headers=headers(ADMIN_A),
        )
        assert response.status_code == 201
    app.state.engine.dispose()
    key = tmp_path / "backup.key"
    backup.write_new(key, os.urandom(32))
    return source, key, tmp_path / "snapshot.local-backup.enc"


def test_backup_restores_database_originals_and_application_key(workspace, tmp_path):
    source, key, archive = workspace
    result = backup.create_local_backup(source, archive, key)
    assert result["original_files"] == 1
    encrypted = archive.read_bytes()
    assert b"confidential backup policy" not in encrypted
    assert (source / "master.key").read_bytes() not in encrypted
    assert archive.stat().st_mode & 0o777 == 0o600
    files = backup.verify_archive(encrypted, backup.read_key(key))
    assert len(files) == 3
    target = tmp_path / "recovery-drill"
    assert backup.restore_local_backup(archive, key, target)["original_files"] == 1
    assert (target / "master.key").read_bytes() == (source / "master.key").read_bytes()
    with sqlite3.connect(target / "enterprise.db") as database:
        assert database.execute("SELECT count(*) FROM document_versions").fetchone() == (1,)
        tenant_id = database.execute("SELECT id FROM tenants").fetchone()[0]
        version_id = database.execute("SELECT id FROM document_versions").fetchone()[0]
    original = next((source / "uploads").rglob("payload.enc"))
    assert (target / original.relative_to(source)).read_bytes() == original.read_bytes()
    # Open the restored instance, apply normal startup checks, and read authorized content.
    app = create_app(f"sqlite:///{target / 'enterprise.db'}", storage_root=str(target / "uploads"))
    with TestClient(app) as client:
        response = client.get(
            f"/v1/tenants/{tenant_id}/document-versions/{version_id}/chunks",
            headers=headers(ADMIN_A),
        )
        assert response.status_code == 200
        assert "confidential backup policy" in response.text
    app.state.engine.dispose()


def test_snapshot_includes_committed_wal_data(workspace):
    source, key, archive = workspace
    with sqlite3.connect(source / "enterprise.db") as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE document_versions SET version_number=2")
        writer.commit()
        backup.create_local_backup(source, archive, key)
        files = backup.verify_archive(archive.read_bytes(), backup.read_key(key))
        copy = sqlite3.connect(":memory:")
        try:
            copy.deserialize(files["enterprise.db"])
            assert copy.execute("SELECT version_number FROM document_versions").fetchone() == (2,)
        finally:
            copy.close()


def test_wrong_backup_key_and_tampering_never_create_restore_target(workspace, tmp_path):
    source, key, archive = workspace
    backup.create_local_backup(source, archive, key)
    with pytest.raises(backup.LocalBackupError):
        backup.verify_archive(archive.read_bytes(), os.urandom(32))
    encrypted = bytearray(archive.read_bytes())
    encrypted[-1] ^= 1
    archive.write_bytes(encrypted)
    target = tmp_path / "never-created"
    with pytest.raises(backup.LocalBackupError):
        backup.restore_local_backup(archive, key, target)
    assert not target.exists()


def test_missing_original_aborts_backup_without_publishing(workspace):
    source, key, archive = workspace
    next((source / "uploads").rglob("payload.enc")).unlink()
    with pytest.raises(backup.LocalBackupError):
        backup.create_local_backup(source, archive, key)
    assert not archive.exists()


def test_wrong_master_key_rejects_backup(workspace):
    source, key, archive = workspace
    (source / "master.key").write_text("wrong-but-long-enough-" * 4)
    with pytest.raises(backup.LocalBackupError):
        backup.create_local_backup(source, archive, key)
    assert not archive.exists()


def test_backup_and_restore_never_overwrite_existing_paths(workspace):
    source, key, archive = workspace
    backup.create_local_backup(source, archive, key)
    original = archive.read_bytes()
    with pytest.raises(backup.LocalBackupError):
        backup.create_local_backup(source, archive, key)
    with pytest.raises(backup.LocalBackupError):
        backup.restore_local_backup(archive, key, source)
    with pytest.raises(FileExistsError):
        backup.write_new(key, os.urandom(32))
    assert archive.read_bytes() == original


def test_symlinked_original_is_rejected(workspace, tmp_path):
    source, key, archive = workspace
    original = next((source / "uploads").rglob("payload.enc"))
    elsewhere = tmp_path / "elsewhere.enc"
    original.rename(elsewhere)
    original.symlink_to(elsewhere)
    with pytest.raises(backup.LocalBackupError):
        backup.create_local_backup(source, archive, key)


def test_archive_traversal_is_rejected_even_with_valid_encryption(workspace, tmp_path):
    source, key_file, archive = workspace
    backup.create_local_backup(source, archive, key_file)
    key = backup.read_key(key_file)
    raw = AESGCM(key).decrypt(archive.read_bytes()[5:17], archive.read_bytes()[17:], backup.CONTEXT)
    target_stream = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as old, zipfile.ZipFile(target_stream, "w") as new:
        manifest = json.loads(old.read("manifest.json"))
        manifest["sha256"]["../escape"] = backup.hashlib.sha256(b"bad").hexdigest()
        for name in old.namelist():
            new.writestr(name, json.dumps(manifest) if name == "manifest.json" else old.read(name))
        new.writestr("../escape", b"bad")
    nonce = os.urandom(12)
    archive.write_bytes(
        backup.MAGIC + nonce + AESGCM(key).encrypt(nonce, target_stream.getvalue(), backup.CONTEXT)
    )
    with pytest.raises(backup.LocalBackupError):
        backup.restore_local_backup(archive, key_file, tmp_path / "restored")
    assert not (tmp_path / "escape").exists()


def test_corrupted_original_cannot_be_backed_up(workspace):
    source, key, archive = workspace
    original = next((source / "uploads").rglob("payload.enc"))
    original.write_bytes(b"corrupt original")
    with pytest.raises(backup.LocalBackupError):
        backup.create_local_backup(source, archive, key)
