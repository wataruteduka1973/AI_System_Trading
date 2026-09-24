"""OIDC Authorization Code + PKCE against an external IdP the customer
provides (docs/plans/horizon5-implementation-plan.md Unit 3). No
self-contained IdP is implemented -- see the plan's scope note (§0.3): user
registration, password reset, and MFA would each need to be built from
scratch for that, which is out of proportion to this Horizon's other items.

PKCE follows RFC 7636 §4.1-4.2 (https://www.rfc-editor.org/rfc/rfc7636).
Discovery follows OpenID Connect Discovery 1.0 §4
(https://openid.net/specs/openid-connect-discovery-1_0.html#ProviderConfig).
"""

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient


class OidcError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PkcePair:
    code_verifier: str
    code_challenge: str


def generate_pkce_pair() -> PkcePair:
    """RFC 7636 §4.1-4.2: `code_verifier` is 43-128 chars of
    `[A-Za-z0-9-._~]`; `token_urlsafe(64)` produces ~86 base64url chars,
    comfortably inside that range. `code_challenge` is the SHA-256 digest of
    the verifier, base64url-encoded without padding (`method=S256`)."""
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return PkcePair(code_verifier=code_verifier, code_challenge=code_challenge)


def generate_state() -> str:
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class DiscoveryDocument:
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    issuer: str


async def fetch_discovery_document(issuer: str) -> DiscoveryDocument:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{issuer.rstrip('/')}/.well-known/openid-configuration")
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        # /code-review finding: connection failures, timeouts, and non-2xx
        # responses (httpx.HTTPError covers both httpx.RequestError and
        # httpx.HTTPStatusError) previously propagated raw out of this
        # function -- callers only caught OidcError, so this surfaced as an
        # unstructured 500 instead of the same handled error shape as every
        # other IdP-communication failure.
        raise OidcError(
            "discovery_unavailable", "Could not reach the IdP's discovery endpoint"
        ) from exc
    try:
        return DiscoveryDocument(
            authorization_endpoint=data["authorization_endpoint"],
            token_endpoint=data["token_endpoint"],
            jwks_uri=data["jwks_uri"],
            issuer=data.get("issuer", issuer),
        )
    except KeyError as exc:
        raise OidcError(
            "discovery_malformed", "IdP discovery document is missing a required field"
        ) from exc


def build_authorization_url(
    discovery: DiscoveryDocument,
    *,
    client_id: str,
    redirect_uri: str,
    scopes: str,
    state: str,
    code_challenge: str,
    nonce: str,
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "nonce": nonce,
    }
    return f"{discovery.authorization_endpoint}?{urlencode(params)}"


async def exchange_code_for_tokens(
    token_endpoint: str,
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
) -> dict[str, str]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code_verifier": code_verifier,
                },
            )
    except httpx.HTTPError as exc:
        # /code-review finding, same as fetch_discovery_document above: a raw
        # connection failure or timeout here used to propagate uncaught.
        raise OidcError(
            "token_exchange_unavailable", "Could not reach the IdP's token endpoint"
        ) from exc
    if response.status_code >= 400:
        raise OidcError("token_exchange_failed", "IdP rejected the authorization code exchange")
    return response.json()


@lru_cache(maxsize=8)
def _jwks_client(jwks_uri: str) -> PyJWKClient:
    """Cached per `jwks_uri` (/code-review finding): `verify_id_token` used to
    construct a fresh `PyJWKClient` on every call, so its own internal JWKS
    cache never had a chance to be reused -- every login re-fetched the IdP's
    full key set. A self-hosted deployment configures exactly one IdP (one
    `jwks_uri`), so `maxsize=8` is generous headroom, not a real bound."""
    return PyJWKClient(jwks_uri)


def verify_id_token(
    id_token: str, *, jwks_uri: str, audience: str, issuer: str, nonce: str
) -> dict[str, object]:
    """Validates signature (RS256, via the IdP's published JWKS), audience,
    issuer, and the `nonce` claim against the value minted at `/auth/login`
    and carried through the `oidc_state` cookie. Raises OidcError on any
    failure -- never returns a partially trusted payload."""
    try:
        jwks_client = _jwks_client(jwks_uri)
        signing_key = jwks_client.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
        )
    except jwt.PyJWTError as exc:
        raise OidcError("id_token_invalid", "ID token failed verification") from exc
    if claims.get("nonce") != nonce:
        raise OidcError("id_token_invalid", "ID token nonce mismatch")
    return claims


_STATE_ALGORITHM = "HS256"
STATE_TTL_SECONDS = 600
"""10 minutes: long enough for a customer to authenticate at their IdP,
short enough that an intercepted oidc_state cookie is useless soon after."""


@dataclass(frozen=True)
class PendingLogin:
    state: str
    code_verifier: str
    nonce: str


def sign_pending_login(pending: PendingLogin, *, secret: str) -> str:
    """Packs (state, code_verifier, nonce) into a signed, short-lived token
    for the `oidc_state` cookie -- see app/api/routes/auth.py's module
    docstring for why this needs to be a cookie at all (no server-side
    session store exists yet at the point login starts)."""
    now = datetime.now(UTC).replace(microsecond=0)
    payload = {
        "state": pending.state,
        "code_verifier": pending.code_verifier,
        "nonce": pending.nonce,
        "iat": now,
        "exp": now + timedelta(seconds=STATE_TTL_SECONDS),
    }
    return jwt.encode(payload, secret, algorithm=_STATE_ALGORITHM)


def verify_pending_login(token: str, *, secret: str) -> PendingLogin:
    try:
        payload = jwt.decode(token, secret, algorithms=[_STATE_ALGORITHM])
        return PendingLogin(
            state=payload["state"],
            code_verifier=payload["code_verifier"],
            nonce=payload["nonce"],
        )
    except jwt.PyJWTError as exc:
        raise OidcError("oidc_state_invalid", "Login attempt has expired or is invalid") from exc
    except (KeyError, TypeError) as exc:
        raise OidcError("oidc_state_invalid", "Login attempt has expired or is invalid") from exc
