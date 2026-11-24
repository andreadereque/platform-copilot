"""A fake identity provider for the tests: an RSA key generated here, its public JWKS served by
an httpx.MockTransport (no network, no Keycloak), and tokens signed locally.

The API validates them exactly as it validates Keycloak's: RS256, kid -> JWKS, iss, aud, exp.
"""

import asyncio
import json
import time
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from platform_copilot.auth import JwksCache, OidcConfig, TokenValidator
from platform_copilot.config import Settings
from platform_copilot.seed import SEED_DIR

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KID = "test-key-1"
CONFIG = OidcConfig.from_settings(Settings())
SEED_USERS = {user["id"]: user for user in json.loads((SEED_DIR / "users.json").read_text())}


def jwks(key: rsa.RSAPrivateKey = KEY, kid: str = KID) -> dict[str, Any]:
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {"keys": [{**public, "kid": kid, "use": "sig", "alg": "RS256"}]}


class FakeJwksEndpoint:
    """Serves a JWKS like Keycloak's certs endpoint and counts how often it is asked."""

    def __init__(self, body: Any = None, status: int = 200) -> None:
        self.body = body
        if body is None:
            self.body = jwks()
        self.status = status
        self.calls = 0
        self.transport = httpx.MockTransport(self.handle)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        await asyncio.sleep(0)  # like a real network call, let other requests run meanwhile
        assert str(request.url) == CONFIG.jwks_url
        if isinstance(self.body, str):
            return httpx.Response(self.status, text=self.body)
        return httpx.Response(self.status, json=self.body)


def make_validator(endpoint: FakeJwksEndpoint | None = None) -> TokenValidator:
    if endpoint is None:
        endpoint = FakeJwksEndpoint()
    return TokenValidator(CONFIG, JwksCache(CONFIG.jwks_url, endpoint.transport))


def make_token(
    claims: dict[str, Any] | None = None,
    *,
    key: rsa.RSAPrivateKey = KEY,
    kid: str | None = KID,
    drop: tuple[str, ...] = (),
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": CONFIG.issuer,
        "aud": CONFIG.audience,
        "sub": "8f0c-user",
        "iat": now,
        "exp": now + 300,
        "preferred_username": "laura@sales",
        "name": "Laura Gil",
        "groups": ["/sales", "/public"],
    }
    payload.update(claims or {})
    for claim in drop:
        payload.pop(claim)
    headers = {}
    if kid is not None:
        headers["kid"] = kid
    return jwt.encode(payload, key, algorithm="RS256", headers=headers)


def bearer(user_id: str) -> dict[str, str]:
    """Authorization header for one of the seeded fake users, with their name and groups."""
    user = SEED_USERS.get(user_id, {"name": user_id, "groups": ["public"]})
    claims = {"preferred_username": user_id, "name": user["name"], "groups": user["groups"]}
    return {"Authorization": f"Bearer {make_token(claims)}"}
