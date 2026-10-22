"""Local ClamAV INSTREAM adapter. Only an explicit clean result permits ingestion."""

import ipaddress
import math
import os
import socket
import struct
import time
from typing import Protocol


class FileScanUnavailable(RuntimeError):
    pass


class UnsafeFile(RuntimeError):
    pass


class FileScanner(Protocol):
    name: str

    def scan(self, content: bytes) -> None: ...


class DisabledFileScanner:
    name = "disabled"

    def scan(self, content: bytes) -> None:
        pass


class ClamAVScanner:
    name = "clamav"

    def __init__(self, host: str = "127.0.0.1", port: int = 3310, timeout: float = 30):
        # Clamd TCP has neither encryption nor authentication. Avoid DNS and remote transfers.
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError("CLAMAV_HOST must be a literal loopback IP")
        if not 1 <= port <= 65535 or not math.isfinite(timeout) or not 1 <= timeout <= 120:
            raise ValueError("Invalid ClamAV port or timeout (1–120 seconds)")
        self.host, self.port, self.timeout = host, port, timeout

    def scan(self, content: bytes) -> None:
        deadline = time.monotonic() + self.timeout

        def remaining() -> float:
            value = deadline - time.monotonic()
            if value <= 0:
                raise FileScanUnavailable("File scan timed out")
            return value

        try:
            with socket.create_connection(
                (self.host, self.port), timeout=remaining()
            ) as connection:
                connection.settimeout(remaining())
                connection.sendall(b"zINSTREAM\0")
                for start in range(0, len(content), 65536):
                    chunk = content[start : start + 65536]
                    connection.settimeout(remaining())
                    connection.sendall(struct.pack("!I", len(chunk)) + chunk)
                connection.settimeout(remaining())
                connection.sendall(b"\0\0\0\0")
                response = bytearray()
                while b"\0" not in response:
                    connection.settimeout(remaining())
                    chunk = connection.recv(4096 - len(response))
                    if not chunk:
                        raise FileScanUnavailable("Incomplete file scan result")
                    response.extend(chunk)
                    if len(response) >= 4096:
                        raise FileScanUnavailable("Invalid file scan response")
        except OSError as error:
            raise FileScanUnavailable("File scanner is unavailable") from error
        if bytes(response) == b"stream: OK\0":
            return
        if bytes(response).startswith(b"stream: ") and bytes(response).endswith(b" FOUND\0"):
            raise UnsafeFile("Upload blocked by file security scan")
        # Do not reflect daemon diagnostics/signatures/file contents to clients or logs.
        raise FileScanUnavailable("File scanner did not return an explicit clean result")


def scanner_from_environment() -> FileScanner:
    backend = os.getenv("FILE_SCAN_BACKEND", "disabled").strip().lower()
    required = os.getenv("FILE_SCAN_REQUIRED", "false").lower() in {"1", "true", "yes"}
    if backend == "disabled" and not required:
        return DisabledFileScanner()
    if backend != "clamav":
        raise RuntimeError("Configure FILE_SCAN_BACKEND=clamav when file scanning is required")
    try:
        return ClamAVScanner(
            host=os.getenv("CLAMAV_HOST", "127.0.0.1"),
            port=int(os.getenv("CLAMAV_PORT", "3310")),
            timeout=float(os.getenv("CLAMAV_TIMEOUT_SECONDS", "30")),
        )
    except ValueError as error:
        raise RuntimeError("Invalid local ClamAV configuration") from error
