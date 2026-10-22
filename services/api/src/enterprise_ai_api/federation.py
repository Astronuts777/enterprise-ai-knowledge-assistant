"""Opt-in OIDC configuration and strict, bounded identity-provider transport."""

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID

import jwt


@dataclass(frozen=True)
class Provider:
    key: str
    tenant_id: UUID
    issuer: str
    client_id: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    client_secret_env: str
    scim_token_env: str
    display_name: str = "企业登录"


def load_providers():
    path = os.getenv("SSO_CONFIG_FILE")
    if not path:
        return {}
    entries = json.loads(Path(path).read_text())
    if not isinstance(entries, list) or len(entries) > 100:
        raise RuntimeError("SSO_CONFIG_FILE must be an array with at most 100 providers")
    result = {}
    for item in entries:
        item = dict(item)
        item["tenant_id"] = UUID(item["tenant_id"])
        provider = Provider(**item)
        if not re.fullmatch(r"[a-z0-9-]{1,64}", provider.key) or provider.key in result:
            raise RuntimeError("SSO provider keys must be unique lowercase slugs")
        origin = urlsplit(provider.issuer)
        for value in (
            provider.issuer,
            provider.authorization_endpoint,
            provider.token_endpoint,
            provider.jwks_uri,
        ):
            parsed = urlsplit(value)
            local = (
                os.getenv("APP_ENV", "development") == "development"
                and os.getenv("SSO_ALLOW_LOOPBACK_HTTP") == "true"
                and parsed.hostname in {"127.0.0.1", "::1"}
            )
            if (
                not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))
                or (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc)
            ):
                raise RuntimeError("OIDC endpoints must use HTTPS and the configured issuer origin")
        if (
            not os.getenv(provider.client_secret_env)
            or len(os.getenv(provider.scim_token_env, "")) < 32
        ):
            raise RuntimeError(
                "OIDC client secret and a SCIM token of at least 32 characters are required"
            )
        result[provider.key] = provider
    return result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class IdentityTransport:
    def request(self, url, data=None):
        body = urlencode(data).encode() if data is not None else None
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        with build_opener(ProxyHandler({}), NoRedirect()).open(
            Request(url, data=body, headers=headers), timeout=15
        ) as response:
            payload = response.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024:
            raise ValueError("Identity response exceeds limit")
        result = json.loads(payload)
        if not isinstance(result, dict):
            raise ValueError("Invalid identity response")
        return result


def subject_hash(subject):
    # OIDC subjects are case-sensitive; never use the email-normalizing blind index.
    return hashlib.sha256(subject.encode()).hexdigest()


def verify_id_token(encoded, provider, nonce, jwks):
    if not isinstance(encoded, str) or len(encoded) > 32768:
        raise ValueError("Invalid ID token")
    header = jwt.get_unverified_header(encoded)
    if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
        raise ValueError("Only configured RS256 keys are accepted")
    keys = [
        key
        for key in jwks.get("keys", [])
        if key.get("kid") == header["kid"]
        and key.get("kty") == "RSA"
        and key.get("use", "sig") == "sig"
        and key.get("alg", "RS256") == "RS256"
    ]
    if len(keys) != 1:
        raise ValueError("Unknown or ambiguous signing key")
    key = jwt.PyJWK.from_dict(keys[0], algorithm="RS256").key
    claims = jwt.decode(
        encoded,
        key,
        algorithms=["RS256"],
        audience=provider.client_id,
        issuer=provider.issuer,
        options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]},
    )
    if claims["nonce"] != nonce or not claims["sub"] or len(claims["sub"]) > 255:
        raise ValueError("Invalid nonce or subject")
    audience = claims["aud"]
    if ("azp" in claims and claims["azp"] != provider.client_id) or (
        isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != provider.client_id
    ):
        raise ValueError("Invalid authorized party")
    if claims.get("email_verified") is not True or not isinstance(claims.get("email"), str):
        raise ValueError("A verified email is required")
    return claims
