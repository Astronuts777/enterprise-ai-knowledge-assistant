"""Encrypted-object persistence backends for local development and S3-compatible clouds."""

import os
from pathlib import Path, PurePosixPath
from typing import Protocol
from uuid import uuid4

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


class ObjectStorageError(RuntimeError):
    """Raised when an object cannot be persisted or removed."""


class ObjectStorage(Protocol):
    backend_name: str

    def put(self, key: str, payload: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...


def _validated_key(key: str) -> PurePosixPath:
    candidate = PurePosixPath(key)
    if not key or candidate.is_absolute() or ".." in candidate.parts:
        raise ObjectStorageError("Object key is invalid")
    return candidate


class LocalObjectStorage:
    """Atomic filesystem storage used for development and tests."""

    backend_name = "local"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        candidate = self.root.joinpath(*_validated_key(key).parts).resolve()
        if not candidate.is_relative_to(self.root):
            raise ObjectStorageError("Object key escapes the storage root")
        return candidate

    def put(self, key: str, payload: bytes) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(payload)
            temporary.replace(target)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise ObjectStorageError("Could not persist the uploaded object") from error

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except OSError as error:
            raise ObjectStorageError("Could not read the requested object") from error

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError as error:
            raise ObjectStorageError("Could not delete the requested object") from error


class S3ObjectStorage:
    """Private S3-compatible bucket storage with an optional namespace prefix."""

    backend_name = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        client: BaseClient,
        prefix: str = "",
        server_side_encryption: str | None = None,
    ) -> None:
        if not bucket.strip():
            raise RuntimeError("OBJECT_STORAGE_BUCKET is required for the S3 backend")
        self.bucket = bucket.strip()
        self.client = client
        self.prefix = prefix.strip("/")
        self.server_side_encryption = server_side_encryption

    def _key(self, key: str) -> str:
        validated = str(_validated_key(key))
        return f"{self.prefix}/{validated}" if self.prefix else validated

    def put(self, key: str, payload: bytes) -> None:
        options: dict[str, object] = {
            "Bucket": self.bucket,
            "Key": self._key(key),
            "Body": payload,
            "ContentType": "application/octet-stream",
            "Metadata": {"content-protection": "aes-256-gcm"},
        }
        if self.server_side_encryption:
            options["ServerSideEncryption"] = self.server_side_encryption
        try:
            self.client.put_object(**options)
        except (BotoCoreError, ClientError) as error:
            raise ObjectStorageError("Could not persist the uploaded object") from error

    def get(self, key: str) -> bytes:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
            return response["Body"].read()
        except (BotoCoreError, ClientError, OSError, KeyError) as error:
            raise ObjectStorageError("Could not read the requested object") from error

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self._key(key))
        except (BotoCoreError, ClientError) as error:
            raise ObjectStorageError("Could not delete the requested object") from error


def object_storage_from_environment(*, local_root: Path | None = None) -> ObjectStorage:
    backend = os.getenv("OBJECT_STORAGE_BACKEND", "local").strip().lower()
    if backend == "local":
        root = local_root or Path(
            os.getenv("LOCAL_STORAGE_PATH", "data/local/uploads")
        ).resolve()
        return LocalObjectStorage(root)
    if backend != "s3":
        raise RuntimeError("OBJECT_STORAGE_BACKEND must be either 'local' or 's3'")

    required = {
        "OBJECT_STORAGE_ACCESS_KEY": os.getenv("OBJECT_STORAGE_ACCESS_KEY"),
        "OBJECT_STORAGE_SECRET_KEY": os.getenv("OBJECT_STORAGE_SECRET_KEY"),
        "OBJECT_STORAGE_BUCKET": os.getenv("OBJECT_STORAGE_BUCKET"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing S3 object storage settings: {', '.join(missing)}")

    force_path_style = os.getenv("OBJECT_STORAGE_FORCE_PATH_STYLE", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    client = boto3.client(
        "s3",
        endpoint_url=os.getenv("OBJECT_STORAGE_ENDPOINT") or None,
        aws_access_key_id=required["OBJECT_STORAGE_ACCESS_KEY"],
        aws_secret_access_key=required["OBJECT_STORAGE_SECRET_KEY"],
        region_name=os.getenv("OBJECT_STORAGE_REGION", "us-east-1"),
        config=Config(
            signature_version="s3v4",
            connect_timeout=5,
            read_timeout=30,
            retries={"mode": "standard", "max_attempts": 3},
            s3={"addressing_style": "path" if force_path_style else "auto"},
        ),
    )
    return S3ObjectStorage(
        bucket=required["OBJECT_STORAGE_BUCKET"] or "",
        client=client,
        prefix=os.getenv("OBJECT_STORAGE_PREFIX", ""),
        server_side_encryption=os.getenv("OBJECT_STORAGE_SSE") or None,
    )
