from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from enterprise_ai_api.main import create_app
from enterprise_ai_api.object_storage import (
    LocalObjectStorage,
    ObjectStorageError,
    S3ObjectStorage,
    object_storage_from_environment,
)

ADMIN = UUID("00000000-0000-0000-0000-00000000000a")


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.last_put: dict[str, object] = {}

    def put_object(self, **options: object) -> None:
        self.last_put = options
        self.objects[(str(options["Bucket"]), str(options["Key"]))] = bytes(options["Body"])

    def get_object(self, **options: object) -> dict[str, BytesIO]:
        payload = self.objects[(str(options["Bucket"]), str(options["Key"]))]
        return {"Body": BytesIO(payload)}

    def delete_object(self, **options: object) -> None:
        self.objects.pop((str(options["Bucket"]), str(options["Key"])), None)


class FailingObjectStorage:
    backend_name = "failing-test"

    def put(self, key: str, payload: bytes) -> None:
        raise ObjectStorageError("simulated outage")

    def get(self, key: str) -> bytes:
        raise ObjectStorageError("simulated outage")

    def delete(self, key: str) -> None:
        raise ObjectStorageError("simulated outage")


def test_local_storage_writes_atomically_and_rejects_traversal(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path / "objects")
    storage.put("tenant/document/payload.enc", b"encrypted")

    assert storage.get("tenant/document/payload.enc") == b"encrypted"
    assert not list((tmp_path / "objects").rglob("*.tmp"))
    with pytest.raises(ObjectStorageError):
        storage.put("../outside.enc", b"blocked")

    storage.delete("tenant/document/payload.enc")
    with pytest.raises(ObjectStorageError):
        storage.get("tenant/document/payload.enc")


def test_s3_storage_keeps_bucket_private_key_prefix_and_encryption_metadata() -> None:
    client = FakeS3Client()
    storage = S3ObjectStorage(
        bucket="company-documents",
        client=client,  # type: ignore[arg-type]
        prefix="production/app",
        server_side_encryption="AES256",
    )

    storage.put("tenant/version/payload.enc", b"ciphertext")

    assert client.last_put == {
        "Bucket": "company-documents",
        "Key": "production/app/tenant/version/payload.enc",
        "Body": b"ciphertext",
        "ContentType": "application/octet-stream",
        "Metadata": {"content-protection": "aes-256-gcm"},
        "ServerSideEncryption": "AES256",
    }
    assert storage.get("tenant/version/payload.enc") == b"ciphertext"
    storage.delete("tenant/version/payload.enc")
    assert client.objects == {}


def test_s3_configuration_fails_closed_when_credentials_are_missing(monkeypatch) -> None:
    monkeypatch.setenv("OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.delenv("OBJECT_STORAGE_ACCESS_KEY", raising=False)
    monkeypatch.delenv("OBJECT_STORAGE_SECRET_KEY", raising=False)
    monkeypatch.delenv("OBJECT_STORAGE_BUCKET", raising=False)

    with pytest.raises(RuntimeError, match="Missing S3 object storage settings"):
        object_storage_from_environment()


def test_upload_rolls_back_when_object_storage_is_unavailable() -> None:
    app = create_app("sqlite://", object_storage=FailingObjectStorage())
    headers = {"X-Dev-User-Id": str(ADMIN)}
    with TestClient(app) as client:
        tenant = client.post("/v1/tenants", json={"name": "Acme"}, headers=headers).json()
        space = client.post(
            f"/v1/tenants/{tenant['id']}/spaces",
            json={"name": "Policies", "classification": "confidential"},
            headers=headers,
        ).json()
        uploaded = client.post(
            f"/v1/tenants/{tenant['id']}/spaces/{space['id']}/documents",
            files={"file": ("policy.txt", b"confidential")},
            headers=headers,
        )
        documents = client.get(
            f"/v1/tenants/{tenant['id']}/spaces/{space['id']}/documents",
            headers=headers,
        )

    assert uploaded.status_code == 503
    assert uploaded.json() == {
        "detail": "Encrypted object storage is temporarily unavailable"
    }
    assert documents.json() == []
