import json
import time
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_documents_api import ADMIN_A, create_workspace

from enterprise_ai_api.federation import Provider, verify_id_token
from enterprise_ai_api.federation_routes import SCIM_PATCH, SCIM_USER
from enterprise_ai_api.main import create_app
from enterprise_ai_api.models import FederatedIdentity, Membership, User


@pytest.fixture
def federation(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_SCIM_TOKEN", "s" * 48)
    monkeypatch.setenv("TEST_OIDC_SECRET", "test-secret")
    app = create_app("sqlite://", storage_root=str(tmp_path / "uploads"))
    client = TestClient(app)
    tenant, space = create_workspace(client, ADMIN_A, "Enterprise federation")
    app.state.account_auth = True
    provider = Provider(
        "company",
        UUID(tenant),
        "https://id.test/realm",
        "web",
        "https://id.test/auth",
        "https://id.test/token",
        "https://id.test/keys",
        "TEST_OIDC_SECRET",
        "TEST_SCIM_TOKEN",
    )
    app.state.identity_providers = {"company": provider}
    app.state.public_url = "http://testserver"
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    jwk.update(kid="key1", use="sig", alg="RS256")
    yield app, client, provider, private, {"keys": [jwk]}
    client.close()


def auth():
    return {"Authorization": "Bearer " + "s" * 48}


def provision(client, subject="subject-A", email="worker@example.test"):
    return client.post(
        "/scim/v2/company/Users",
        headers=auth(),
        json={"schemas": [SCIM_USER], "externalId": subject, "userName": email, "active": True},
    )


def claims(provider, nonce="nonce", **changes):
    result = {
        "iss": provider.issuer,
        "aud": provider.client_id,
        "sub": "subject-A",
        "nonce": nonce,
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
        "email": "worker@example.test",
        "email_verified": True,
    }
    result.update(changes)
    return result


def sign(private, payload):
    return jwt.encode(payload, private, algorithm="RS256", headers={"kid": "key1"})


def begin(fixture, overrides=None):
    app, client, provider, private, jwks = fixture
    response = client.get("/v1/auth/sso/company/login", follow_redirects=False)
    assert response.status_code == 302
    params = parse_qs(urlsplit(response.headers["location"]).query)
    assert params["code_challenge_method"] == ["S256"]
    token = sign(private, claims(provider, params["nonce"][0], **(overrides or {})))

    class Transport:
        def request(self, url, data=None):
            if url == provider.jwks_uri:
                return jwks
            assert data["code_verifier"]
            assert data["redirect_uri"] == "http://testserver/v1/auth/sso/company/callback"
            return {"id_token": token}

    app.state.identity_transport = Transport()
    return "/v1/auth/sso/company/callback?" + "state=" + params["state"][0] + "&code=test-code"


def test_scim_requires_bearer_and_never_email_links_or_assigns_admin(federation):
    app, client, provider, _, _ = federation
    assert client.get("/scim/v2/company/Users").status_code == 401
    result = provision(client)
    assert result.status_code == 201
    assert provision(client, subject="other-subject").status_code == 409
    assert provision(client, subject="subject-A", email="other@example.test").status_code == 409
    with app.state.session_factory() as session:
        identity = session.scalar(select(FederatedIdentity))
        assert session.get(Membership, (provider.tenant_id, identity.user_id)).role == "member"
        assert session.get(User, identity.user_id).password_hash is None
    listed = client.get(
        "/scim/v2/company/Users?filter=userName%20eq%20%22worker@example.test%22", headers=auth()
    ).json()
    assert listed["totalResults"] == 1
    assert (
        client.get("/scim/v2/company/Users?filter=unsupported", headers=auth()).status_code == 400
    )
    bad = client.patch(
        f"/scim/v2/company/Users/{result.json()['id']}",
        headers=auth(),
        json={
            "schemas": [SCIM_PATCH],
            "Operations": [{"op": "replace", "value": {"role": "enterprise_admin"}}],
        },
    )
    assert bad.status_code == 400


def test_sso_preprovisioned_login_and_state_replay_denied(federation):
    app, client, provider, _, _ = federation
    assert provision(client).status_code == 201
    callback = begin(federation)
    response = client.get(callback, follow_redirects=False)
    assert response.status_code == 302
    account = client.get("/v1/auth/me").json()
    assert account["email"] == "worker@example.test"
    assert account["password_login_enabled"] is False
    assert client.get(f"/v1/tenants/{provider.tenant_id}").status_code == 200
    assert client.get(callback, follow_redirects=False).status_code == 400
    # SSO-only users can enroll local MFA without inventing a password.
    assert client.post("/v1/auth/mfa/setup", json={"password": "sso"}).status_code == 200


def test_sso_requires_browser_binding_and_preprovisioning(federation):
    app, client, _, _, _ = federation
    callback = begin(federation)
    other = TestClient(app)
    assert other.get(callback, follow_redirects=False).status_code == 400
    assert client.get(callback, follow_redirects=False).status_code == 403


@pytest.mark.parametrize(
    "changes",
    [
        {"iss": "https://evil.test"},
        {"aud": "wrong"},
        {"nonce": "wrong"},
        {"exp": 1},
        {"email_verified": False},
        {"azp": "other"},
        {"aud": ["web", "other"]},
    ],
)
def test_id_token_rejects_invalid_claims(federation, changes):
    _, _, provider, private, jwks = federation
    with pytest.raises((ValueError, jwt.PyJWTError)):
        verify_id_token(sign(private, claims(provider, **changes)), provider, "nonce", jwks)


def test_scim_deactivation_revokes_old_sessions_and_cannot_undo_manual_suspension(federation):
    app, client, provider, _, _ = federation
    resource = provision(client).json()
    callback = begin(federation)
    assert client.get(callback, follow_redirects=False).status_code == 302
    cookie = client.cookies.get("enterprise_ai_session")
    path = f"/scim/v2/company/Users/{resource['id']}"

    def active(value):
        return client.patch(
            path,
            headers=auth(),
            json={
                "schemas": [SCIM_PATCH],
                "Operations": [{"op": "replace", "path": "active", "value": value}],
            },
        )

    assert active(False).status_code == 200
    assert client.get("/v1/auth/me").status_code == 401
    assert client.get(begin(federation), follow_redirects=False).status_code == 403
    with app.state.session_factory() as session:
        identity = session.scalar(select(FederatedIdentity))
        session.get(Membership, (provider.tenant_id, identity.user_id)).active = False
        session.commit()
    assert active(True).status_code == 200
    assert client.get(begin(federation), follow_redirects=False).status_code == 403
    client.cookies.set("enterprise_ai_session", cookie)
    assert client.get("/v1/auth/me").status_code == 401


def test_sso_does_not_bypass_local_mfa(federation):
    app, client, _, _, _ = federation
    provision(client)
    from datetime import UTC, datetime

    from enterprise_ai_api.mfa import new_totp_secret, totp_code

    secret = new_totp_secret()

    with app.state.session_factory() as session:
        identity = session.scalar(select(FederatedIdentity))
        user = session.get(User, identity.user_id)
        user.mfa_enabled_at = datetime.now(UTC)
        user.mfa_secret_ciphertext = secret
        session.commit()
    assert client.get(begin(federation), follow_redirects=False).status_code == 302
    assert client.get("/v1/auth/me").status_code == 401
    challenge = client.post("/v1/auth/sso/complete").json()
    assert challenge["mfa_required"] is True
    assert (
        client.post(
            "/v1/auth/mfa/login",
            json={"challenge_token": challenge["challenge_token"], "code": "invalid"},
        ).status_code
        == 401
    )

    result = client.post(
        "/v1/auth/mfa/login",
        json={
            "challenge_token": challenge["challenge_token"],
            "code": totp_code(secret, int(time.time()) // 30),
        },
    )
    assert result.status_code == 200
    assert client.get("/v1/auth/me").status_code == 200


def test_scim_soft_delete_blocks_login(federation):
    _, client, _, _, _ = federation
    resource = provision(client).json()
    path = f"/scim/v2/company/Users/{resource['id']}"
    assert client.delete(path, headers=auth()).status_code == 204
    assert client.get(path, headers=auth()).status_code == 404
    assert client.get(begin(federation), follow_redirects=False).status_code == 403
    replacement = provision(client)
    assert replacement.status_code == 201
    assert replacement.json()["id"] != resource["id"]


def test_groups_sync_without_granting_access_and_reject_cross_provider_members(
    federation, monkeypatch
):
    from dataclasses import replace

    from enterprise_ai_api.scim_groups import SCIM_GROUP

    app, client, provider, _, _ = federation
    first = provision(client).json()
    second = provision(client, "subject-B", "second@example.test").json()
    payload = {
        "schemas": [SCIM_GROUP],
        "externalId": "staff",
        "displayName": "Staff",
        "members": [{"value": first["id"]}],
    }
    response = client.post("/scim/v2/company/Groups", headers=auth(), json=payload)
    assert response.status_code == 201
    path = "/scim/v2/company/Groups/" + response.json()["id"]
    assert (
        client.patch(
            path,
            headers=auth(),
            json={
                "schemas": [SCIM_PATCH],
                "Operations": [
                    {"op": "add", "path": "members", "value": [{"value": second["id"]}]}
                ],
            },
        ).status_code
        == 200
    )
    assert len(client.get(path, headers=auth()).json()["members"]) == 2
    assert (
        client.patch(
            path,
            headers=auth(),
            json={
                "schemas": [SCIM_PATCH],
                "Operations": [{"op": "remove", "path": f'members[value eq "{second["id"]}"]'}],
            },
        ).status_code
        == 200
    )
    assert len(client.get(path, headers=auth()).json()["members"]) == 1
    # A group with an admin-looking name grants no knowledge-space privileges.
    assert client.get(begin(federation), follow_redirects=False).status_code == 302
    assert client.get(f"/v1/tenants/{provider.tenant_id}/spaces").json() == []
    monkeypatch.setenv("OTHER_SCIM_TOKEN", "x" * 48)
    app.state.identity_providers["other"] = replace(
        provider, key="other", scim_token_env="OTHER_SCIM_TOKEN"
    )
    assert client.get("/scim/v2/other/Users/" + first["id"], headers=auth()).status_code == 401
    other_auth = {"Authorization": "Bearer " + "x" * 48}
    assert client.get("/scim/v2/other/Users/" + first["id"], headers=other_auth).status_code == 404
    assert client.post("/scim/v2/other/Groups", headers=other_auth, json=payload).status_code == 400
    assert client.delete(path, headers=auth()).status_code == 204
    assert client.get(path, headers=auth()).status_code == 404
    assert client.get("/scim/v2/company/Users/" + first["id"], headers=auth()).status_code == 200


def test_scim_discovery_and_errors_use_scim_media_type(federation):
    _, client, _, _, _ = federation
    for endpoint in ("Schemas", "ResourceTypes", "ServiceProviderConfig"):
        response = client.get("/scim/v2/company/" + endpoint, headers=auth())
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/scim+json"
    response = client.get("/scim/v2/company/Users")
    assert response.json()["status"] == "401"
    assert response.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]


def test_signature_tampering_and_missing_claims_rejected(federation):
    _, _, provider, _, jwks = federation
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(jwt.PyJWTError):
        verify_id_token(sign(attacker, claims(provider)), provider, "nonce", jwks)


def test_encryption_prefix_is_not_a_plaintext_bypass():
    from enterprise_ai_api.crypto import decrypt_text, encrypt_text

    plaintext = "enc:v1:this is a literal document or identity value"
    assert decrypt_text(encrypt_text(plaintext)) == plaintext


def test_provider_config_rejects_unsafe_endpoints(tmp_path, monkeypatch):
    from dataclasses import asdict

    from enterprise_ai_api.federation import load_providers

    provider = Provider(
        "company",
        UUID(int=1),
        "https://id.test/realm",
        "web",
        "https://id.test/auth",
        "https://id.test/token",
        "https://id.test/keys",
        "OIDC_SECRET",
        "SCIM_SECRET",
    )
    path = tmp_path / "providers.json"
    monkeypatch.setenv("SSO_CONFIG_FILE", str(path))
    monkeypatch.setenv("OIDC_SECRET", "test-secret")
    monkeypatch.setenv("SCIM_SECRET", "s" * 48)
    payload = asdict(provider)
    payload["tenant_id"] = str(provider.tenant_id)
    path.write_text(json.dumps([payload]))
    assert load_providers()["company"] == provider
    for endpoint in (
        "http://id.test/token",
        "https://attacker.test/token",
        "https://user:password@id.test/token",
        "https://id.test/token?secret=bad",
    ):
        path.write_text(json.dumps([{**payload, "token_endpoint": endpoint}]))
        with pytest.raises(RuntimeError):
            load_providers()


def test_sso_only_account_cannot_use_email_or_password_recovery_to_bypass_idp(federation):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from enterprise_ai_api.auth_routes import _issue_account_token
    from enterprise_ai_api.models import AccountToken

    app, client, _, _, _ = federation
    assert provision(client).status_code == 201
    sender = Mock()
    app.state.email_sender = SimpleNamespace(enabled=True, send=sender)
    for endpoint in ("/v1/auth/email/resend", "/v1/auth/password/forgot"):
        assert client.post(endpoint, json={"email": "worker@example.test"}).status_code == 200
    sender.assert_not_called()
    with app.state.session_factory() as session:
        assert session.scalar(select(AccountToken.id)) is None
        user_id = session.scalar(select(FederatedIdentity.user_id))
        verification = _issue_account_token(session, user_id, "email_verification")
        reset = _issue_account_token(session, user_id, "password_reset")
        session.commit()
    assert client.post("/v1/auth/email/verify", json={"token": verification}).status_code == 400
    assert (
        client.post(
            "/v1/auth/password/reset", json={"token": reset, "password": "not-an-idp-password"}
        ).status_code
        == 400
    )
    assert client.get("/v1/auth/me").status_code == 401


def test_expired_state_cannot_be_exchanged(federation):
    from datetime import UTC, datetime, timedelta

    from enterprise_ai_api.models import OidcTransaction

    app, client, _, _, _ = federation
    provision(client)
    callback = begin(federation)
    with app.state.session_factory() as session:
        session.scalar(select(OidcTransaction)).expires_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
        session.commit()
    assert client.get(callback, follow_redirects=False).status_code == 400
