"""Unit 3 (docs/plans/horizon5-implementation-plan.md): PKCE pair
correctness, discovery/token-exchange over a mocked `httpx` (respx, an
existing dev dependency), and the signed oidc_state pending-login token.
"""

import base64
import hashlib

import httpx
import pytest
import respx
from app.security import oidc

ISSUER = "https://idp.example.com"
STATE_SECRET = "test-oidc-state-secret"


def test_generate_pkce_pair_challenge_matches_verifier() -> None:
    pair = oidc.generate_pkce_pair()
    expected_digest = hashlib.sha256(pair.code_verifier.encode("ascii")).digest()
    expected_challenge = base64.urlsafe_b64encode(expected_digest).decode("ascii").rstrip("=")
    assert pair.code_challenge == expected_challenge
    assert 43 <= len(pair.code_verifier) <= 128


def test_generate_state_is_url_safe_and_nonempty() -> None:
    state = oidc.generate_state()
    assert len(state) > 20
    assert " " not in state


# ---- discovery ----


@respx.mock
@pytest.mark.anyio
async def test_fetch_discovery_document_parses_required_fields() -> None:
    respx.get(f"{ISSUER}/.well-known/openid-configuration").mock(
        return_value=httpx.Response(
            200,
            json={
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/jwks",
                "issuer": ISSUER,
            },
        )
    )
    discovery = await oidc.fetch_discovery_document(ISSUER)
    assert discovery.authorization_endpoint == f"{ISSUER}/authorize"
    assert discovery.token_endpoint == f"{ISSUER}/token"
    assert discovery.jwks_uri == f"{ISSUER}/jwks"


@respx.mock
@pytest.mark.anyio
async def test_fetch_discovery_document_rejects_a_malformed_response() -> None:
    respx.get(f"{ISSUER}/.well-known/openid-configuration").mock(
        return_value=httpx.Response(200, json={"authorization_endpoint": f"{ISSUER}/authorize"})
    )
    with pytest.raises(oidc.OidcError) as exc:
        await oidc.fetch_discovery_document(ISSUER)
    assert exc.value.code == "discovery_malformed"


@respx.mock
@pytest.mark.anyio
async def test_fetch_discovery_document_wraps_a_connection_failure() -> None:
    """Regression test (/code-review finding): a raw httpx.ConnectError (or any
    httpx.HTTPError -- network failure, timeout, non-2xx via raise_for_status)
    used to propagate uncaught instead of becoming a structured OidcError."""
    respx.get(f"{ISSUER}/.well-known/openid-configuration").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    with pytest.raises(oidc.OidcError) as exc:
        await oidc.fetch_discovery_document(ISSUER)
    assert exc.value.code == "discovery_unavailable"


def test_build_authorization_url_includes_pkce_and_state() -> None:
    discovery = oidc.DiscoveryDocument(
        authorization_endpoint=f"{ISSUER}/authorize",
        token_endpoint=f"{ISSUER}/token",
        jwks_uri=f"{ISSUER}/jwks",
        issuer=ISSUER,
    )
    url = oidc.build_authorization_url(
        discovery,
        client_id="client-1",
        redirect_uri="http://localhost/callback",
        scopes="openid email",
        state="the-state",
        code_challenge="the-challenge",
        nonce="the-nonce",
    )
    assert url.startswith(f"{ISSUER}/authorize?")
    assert "state=the-state" in url
    assert "code_challenge=the-challenge" in url
    assert "code_challenge_method=S256" in url
    assert "response_type=code" in url
    assert "nonce=the-nonce" in url


# ---- token exchange ----


@respx.mock
@pytest.mark.anyio
async def test_exchange_code_for_tokens_posts_pkce_verifier() -> None:
    route = respx.post(f"{ISSUER}/token").mock(
        return_value=httpx.Response(200, json={"id_token": "abc", "access_token": "xyz"})
    )
    tokens = await oidc.exchange_code_for_tokens(
        f"{ISSUER}/token",
        code="auth-code",
        code_verifier="verifier-value",
        redirect_uri="http://localhost/callback",
        client_id="client-1",
        client_secret="secret-1",
    )
    assert tokens["id_token"] == "abc"
    sent = route.calls.last.request.content.decode()
    assert "code_verifier=verifier-value" in sent
    assert "grant_type=authorization_code" in sent


@respx.mock
@pytest.mark.anyio
async def test_exchange_code_for_tokens_raises_on_idp_rejection() -> None:
    respx.post(f"{ISSUER}/token").mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )
    with pytest.raises(oidc.OidcError) as exc:
        await oidc.exchange_code_for_tokens(
            f"{ISSUER}/token",
            code="bad-code",
            code_verifier="v",
            redirect_uri="http://localhost/callback",
            client_id="client-1",
            client_secret="secret-1",
        )
    assert exc.value.code == "token_exchange_failed"


@respx.mock
@pytest.mark.anyio
async def test_exchange_code_for_tokens_wraps_a_connection_failure() -> None:
    """Regression test (/code-review finding), same as the discovery case above."""
    respx.post(f"{ISSUER}/token").mock(side_effect=httpx.ConnectTimeout("timed out"))
    with pytest.raises(oidc.OidcError) as exc:
        await oidc.exchange_code_for_tokens(
            f"{ISSUER}/token",
            code="auth-code",
            code_verifier="v",
            redirect_uri="http://localhost/callback",
            client_id="client-1",
            client_secret="secret-1",
        )
    assert exc.value.code == "token_exchange_unavailable"


# ---- verify_id_token's JWKS client cache ----


def test_jwks_client_is_cached_per_jwks_uri() -> None:
    """Regression test (/code-review finding): `verify_id_token` used to build
    a fresh PyJWKClient on every call, so its own internal JWKS cache never
    got reused -- every login re-fetched the IdP's full key set."""
    oidc._jwks_client.cache_clear()
    first = oidc._jwks_client(f"{ISSUER}/jwks")
    second = oidc._jwks_client(f"{ISSUER}/jwks")
    other = oidc._jwks_client("https://other-idp.example.com/jwks")
    assert first is second
    assert first is not other


# ---- pending login (oidc_state) ----


def test_pending_login_round_trip() -> None:
    pending = oidc.PendingLogin(state="the-state", code_verifier="the-verifier", nonce="the-nonce")
    token = oidc.sign_pending_login(pending, secret=STATE_SECRET)
    result = oidc.verify_pending_login(token, secret=STATE_SECRET)
    assert result == pending


def test_pending_login_rejects_a_tampered_secret() -> None:
    pending = oidc.PendingLogin(state="s", code_verifier="v", nonce="n")
    token = oidc.sign_pending_login(pending, secret=STATE_SECRET)
    with pytest.raises(oidc.OidcError) as exc:
        oidc.verify_pending_login(token, secret="a-different-secret-value-entirely")
    assert exc.value.code == "oidc_state_invalid"
