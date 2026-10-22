import socket
import struct
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from test_documents_api import ADMIN_A, ADMIN_B, create_workspace, headers

from enterprise_ai_api import file_scanning as scanning
from enterprise_ai_api.main import create_app


def daemon(monkeypatch, replies):
    connection = Mock()
    connection.recv.side_effect = replies
    context = Mock()
    context.__enter__ = Mock(return_value=connection)
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(scanning.socket, "create_connection", Mock(return_value=context))
    return connection


def test_clamav_stream_protocol_and_fragmented_reply(monkeypatch):
    connection = daemon(monkeypatch, [b"stream: ", b"OK\0"])
    scanning.ClamAVScanner().scan(b"safe content")
    assert [call.args[0] for call in connection.sendall.call_args_list] == [
        b"zINSTREAM\0",
        struct.pack("!I", 12) + b"safe content",
        b"\0\0\0\0",
    ]


@pytest.mark.parametrize("response", [b"stream: virus FOUND\0", b"stream: Eicar-Signature FOUND\0"])
def test_detected_file_is_rejected(monkeypatch, response):
    daemon(monkeypatch, [response])
    with pytest.raises(scanning.UnsafeFile):
        scanning.ClamAVScanner().scan(b"synthetic test data")


@pytest.mark.parametrize(
    "replies",
    [
        [b"stream: OK", b""],
        [b"INSTREAM size limit exceeded ERROR\0"],
        [b"UNKNOWN COMMAND\0"],
        [b""],
        [b"x" * 4096],
        [b"stream: OK\0extra"],
    ],
)
def test_only_complete_explicit_clean_result_passes(monkeypatch, replies):
    daemon(monkeypatch, replies)
    with pytest.raises(scanning.FileScanUnavailable):
        scanning.ClamAVScanner().scan(b"content")


def test_network_failure_does_not_bypass_scan(monkeypatch):
    monkeypatch.setattr(scanning.socket, "create_connection", Mock(side_effect=socket.timeout))
    with pytest.raises(scanning.FileScanUnavailable):
        scanning.ClamAVScanner().scan(b"content")


@pytest.mark.parametrize("host", ["192.168.1.4", "example.com", "localhost", "0.0.0.0"])
def test_scanner_cannot_send_documents_to_remote_host(host):
    with pytest.raises(ValueError):
        scanning.ClamAVScanner(host=host)


def test_required_scan_cannot_be_disabled(monkeypatch):
    monkeypatch.setenv("FILE_SCAN_REQUIRED", "true")
    monkeypatch.setenv("FILE_SCAN_BACKEND", "disabled")
    with pytest.raises(RuntimeError):
        scanning.scanner_from_environment()


@pytest.mark.parametrize(
    "failure,code",
    [
        (scanning.UnsafeFile("private scanner details"), 422),
        (scanning.FileScanUnavailable("private scanner details"), 503),
    ],
)
def test_upload_blocked_before_storage_or_parsing_and_audited(tmp_path, failure, code):
    scanner = Mock(name="scanner")
    scanner.name = "clamav"
    scanner.scan.side_effect = failure
    app = create_app("sqlite://", storage_root=str(tmp_path), file_scanner=scanner)
    with TestClient(app) as client:
        tenant, space = create_workspace(client, ADMIN_A, "Acme")
        response = client.post(
            f"/v1/tenants/{tenant}/spaces/{space}/documents",
            files={"file": ("secret.txt", b"private content")},
            headers=headers(ADMIN_A),
        )
        assert response.status_code == code
        assert "private" not in response.text
        assert (
            client.get(
                f"/v1/tenants/{tenant}/spaces/{space}/documents", headers=headers(ADMIN_A)
            ).json()
            == []
        )
        audit = client.get(f"/v1/tenants/{tenant}/audit-events", headers=headers(ADMIN_A))
        assert "document.upload_blocked" in audit.text
    assert list(tmp_path.rglob("payload.enc")) == []


def test_new_versions_are_scanned_and_unauthorized_uploads_are_not(tmp_path):
    scanner = Mock()
    scanner.name = "clamav"
    app = create_app("sqlite://", storage_root=str(tmp_path), file_scanner=scanner)
    with TestClient(app) as client:
        tenant, space = create_workspace(client, ADMIN_A, "Acme")
        path = f"/v1/tenants/{tenant}/spaces/{space}/documents"
        upload = client.post(
            path, files={"file": ("policy.txt", b"first policy")}, headers=headers(ADMIN_A)
        )
        assert upload.status_code == 201
        document_id = upload.json()["document"]["id"]
        scanner.scan.side_effect = scanning.UnsafeFile()
        response = client.post(
            f"/v1/tenants/{tenant}/documents/{document_id}/versions",
            files={"file": ("new.txt", b"new policy")},
            headers=headers(ADMIN_A),
        )
        assert response.status_code == 422
        scanner.scan.assert_called_with(b"new policy")
        scanner.reset_mock()
        assert client.post(
            path, files={"file": ("x.txt", b"no access")}, headers=headers(ADMIN_B)
        ).status_code in {403, 404}
        scanner.scan.assert_not_called()
    assert len(list(tmp_path.rglob("payload.enc"))) == 1
