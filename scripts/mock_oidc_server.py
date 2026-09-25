"""Local-only mock OIDC provider, for manually verifying the post-login UI in
a browser without needing a real external IdP account. Implements just enough
of Authorization Code + PKCE + Discovery for `app/security/oidc.py` and
`app/api/routes/auth.py` to complete a real login round-trip: discovery,
`/authorize` (auto-approves one fixed test identity -- no login form, since
this script's only job is local manual verification, not acting as a
redistributable test double), `/token` (verifies the PKCE code_verifier for
real, then issues a real RS256-signed id_token).

Not started by scripts/start_local.py and not used by the automated test
suite (tests mock `app.security.oidc` directly instead -- see
tests/test_oidc_client.py). Never point this at a real deployment's
OIDC_ISSUER; it grants a session to anyone who can reach it.

Run: python scripts/mock_oidc_server.py
Then set in .env and restart the backend:
  OIDC_ISSUER=http://127.0.0.1:9000
  OIDC_CLIENT_ID=local-test-client
  OIDC_CLIENT_SECRET=local-test-secret
  SESSION_SIGNING_SECRET=<any random string, e.g. via secrets.token_urlsafe(32)>
"""

import base64
import hashlib
import time
import uuid

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from jwt.algorithms import RSAAlgorithm

ISSUER = "http://127.0.0.1:9000"
KEY_ID = "local-test-key-1"
TEST_USER = {"sub": "local-test-user-1", "email": "test@example.local", "name": "Local Test User"}

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_public_jwk = RSAAlgorithm.to_jwk(_private_key.public_key(), as_dict=True)
_public_jwk["kid"] = KEY_ID
_public_jwk["use"] = "sig"
_public_jwk["alg"] = "RS256"

_pending_codes: dict[str, dict[str, str]] = {}

app = FastAPI()


@app.get("/.well-known/openid-configuration")
def discovery() -> dict[str, str]:
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "jwks_uri": f"{ISSUER}/jwks",
    }


@app.get("/jwks")
def jwks() -> dict[str, list[dict[str, object]]]:
    return {"keys": [_public_jwk]}


@app.get("/authorize")
def authorize(request: Request) -> RedirectResponse:
    params = request.query_params
    code = uuid.uuid4().hex
    _pending_codes[code] = {
        "code_challenge": params["code_challenge"],
        "nonce": params["nonce"],
        "client_id": params["client_id"],
    }
    return RedirectResponse(f"{params['redirect_uri']}?code={code}&state={params['state']}")


@app.post("/token")
async def token(request: Request) -> JSONResponse:
    form = await request.form()
    code = str(form["code"])
    pending = _pending_codes.pop(code, None)
    if pending is None:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    digest = hashlib.sha256(str(form["code_verifier"]).encode("ascii")).digest()
    computed_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    if computed_challenge != pending["code_challenge"]:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    now = int(time.time())
    id_token = jwt.encode(
        {
            "iss": ISSUER,
            "aud": pending["client_id"],
            "sub": TEST_USER["sub"],
            "email": TEST_USER["email"],
            "name": TEST_USER["name"],
            "nonce": pending["nonce"],
            "iat": now,
            "exp": now + 300,
        },
        _private_key,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )
    return JSONResponse(
        {"id_token": id_token, "access_token": "test-access-token", "token_type": "Bearer"}
    )


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9000)
