"""Production posture checks that never reveal secret values."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

from sqlalchemy.engine import make_url

from enterprise_ai_api.backup import (
    BackupError,
    backup_encryption_key_is_valid,
    retention_policy_from_environment,
)
from enterprise_ai_api.database import normalize_database_url


@dataclass(frozen=True)
class OperationalCheck:
    name: str
    passed: bool
    required: bool
    detail: str


@dataclass(frozen=True)
class ProductionPosture:
    production_ready: bool
    checks: tuple[OperationalCheck, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "production_ready": self.production_ready,
            "checks": [asdict(check) for check in self.checks],
        }


def _enabled(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes"}


def _database_is_postgresql() -> bool:
    value = os.getenv("DATABASE_URL", "")
    if not value:
        return False
    try:
        return make_url(normalize_database_url(value)).get_backend_name() == "postgresql"
    except Exception:
        return False


def assess_production_posture() -> ProductionPosture:
    account_auth = _enabled("ACCOUNT_AUTH_MODE")
    email_required = _enabled("REQUIRE_EMAIL_VERIFICATION")
    email_enabled = (
        os.getenv("EMAIL_DELIVERY_BACKEND", "disabled").lower() == "smtp"
        and bool(os.getenv("SMTP_HOST"))
        and bool(os.getenv("SMTP_FROM_EMAIL"))
    )
    public_url = os.getenv("APP_PUBLIC_URL", "")
    object_storage = os.getenv("OBJECT_STORAGE_BACKEND", "local").lower()
    backup_storage = os.getenv("BACKUP_STORAGE_BACKEND", "local").lower()
    object_storage_ready = object_storage == "s3" and all(
        os.getenv(name)
        for name in (
            "OBJECT_STORAGE_ACCESS_KEY",
            "OBJECT_STORAGE_SECRET_KEY",
            "OBJECT_STORAGE_BUCKET",
        )
    )
    backup_storage_ready = backup_storage == "s3" and all(
        os.getenv(name)
        for name in (
            "BACKUP_STORAGE_ACCESS_KEY",
            "BACKUP_STORAGE_SECRET_KEY",
            "BACKUP_STORAGE_BUCKET",
        )
    )

    try:
        retention_policy_from_environment()
        retention_valid = True
    except BackupError:
        retention_valid = False

    checks = (
        OperationalCheck(
            name="application_environment",
            passed=os.getenv("APP_ENV", "development").lower() == "production",
            required=True,
            detail="APP_ENV must be production",
        ),
        OperationalCheck(
            name="postgresql_database",
            passed=_database_is_postgresql(),
            required=True,
            detail="DATABASE_URL must use PostgreSQL",
        ),
        OperationalCheck(
            name="account_authentication",
            passed=account_auth,
            required=True,
            detail="ACCOUNT_AUTH_MODE must be enabled",
        ),
        OperationalCheck(
            name="application_encryption_key",
            passed=bool(os.getenv("ENCRYPTION_MASTER_KEY")),
            required=True,
            detail="ENCRYPTION_MASTER_KEY must be stored in the deployment secret manager",
        ),
        OperationalCheck(
            name="https_public_url",
            passed=public_url.startswith("https://"),
            required=True,
            detail="APP_PUBLIC_URL must use HTTPS",
        ),
        OperationalCheck(
            name="persistent_object_storage",
            passed=bool(object_storage_ready),
            required=True,
            detail="OBJECT_STORAGE_BACKEND must be s3 for enterprise files",
        ),
        OperationalCheck(
            name="file_security_scan",
            passed=(os.getenv("FILE_SCAN_BACKEND", "disabled").lower() == "clamav"
                    and _enabled("FILE_SCAN_REQUIRED")),
            required=True,
            detail="Enable required ClamAV scanning; verify daemon and signatures separately",
        ),
        OperationalCheck(
            name="verified_email_delivery",
            passed=email_required and email_enabled,
            required=True,
            detail="Email verification and SMTP delivery must both be enabled",
        ),
        OperationalCheck(
            name="encrypted_backup_key",
            passed=backup_encryption_key_is_valid(),
            required=True,
            detail="BACKUP_ENCRYPTION_KEY must be configured separately",
        ),
        OperationalCheck(
            name="persistent_backup_storage",
            passed=bool(backup_storage_ready),
            required=True,
            detail="BACKUP_STORAGE_BACKEND must be s3",
        ),
        OperationalCheck(
            name="retention_policy",
            passed=retention_valid,
            required=True,
            detail="Backup and audit retention settings must satisfy minimum policy",
        ),
    )
    return ProductionPosture(
        production_ready=all(check.passed for check in checks if check.required),
        checks=checks,
    )


def main() -> None:
    posture = assess_production_posture()
    print(json.dumps(posture.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    if not posture.production_ready:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
