"""RFC 6238 TOTP helpers and human-readable one-time recovery codes."""

import base64
import hashlib
import hmac
import secrets
import struct
from datetime import UTC, datetime

TOTP_PERIOD_SECONDS = 30
TOTP_DIGITS = 6


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret: str, counter: int) -> str:
    padded = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10**TOTP_DIGITS)).zfill(TOTP_DIGITS)


def verify_totp(
    secret: str,
    code: str,
    *,
    now: datetime | None = None,
    last_counter: int | None = None,
) -> int | None:
    normalized = code.strip().replace(" ", "")
    if not normalized.isdigit() or len(normalized) != TOTP_DIGITS:
        return None
    current = int((now or datetime.now(UTC)).timestamp()) // TOTP_PERIOD_SECONDS
    for counter in range(current - 1, current + 2):
        if (last_counter is None or counter > last_counter) and hmac.compare_digest(
            totp_code(secret, counter), normalized
        ):
            return counter
    return None


def new_recovery_codes(count: int = 8) -> list[str]:
    return [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}".upper() for _ in range(count)]


def normalize_recovery_code(code: str) -> str:
    return code.strip().replace("-", "").replace(" ", "").upper()
