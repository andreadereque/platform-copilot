"""OIDC access-token validation. Tokens are signed here with a test RSA key; the JWKS comes
from a mock transport. No Keycloak, no network."""

import asyncio
import time
from typing import Any

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from tokens import CONFIG, KID, FakeJwksEndpoint, bearer, jwks, make_token, make_validator

from platform_copilot.auth import AuthError, JwksCache, OidcConfig, user_from_claims
from platform_copilot.config import Settings

Conn = psycopg.AsyncConnection[dict[str, object]]
HS_SECRET = "a-shared-secret-of-32-bytes-min!"
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


async def test_a_valid_token_gives_its_claims() -> None:
    claims = await make_validator().validate(make_token())
    assert claims["preferred_username"] == "laura@sales"


@pytest.mark.parametrize(
    ("token", "message"),
    [
        (make_token({"exp": int(time.time()) - 120}), "expired"),
        (make_token({"iss": "http://evil.example/realms/x"}), "(?i)issuer"),
        (make_token({"aud": "another-api"}), "(?i)audience"),
        (make_token(key=OTHER_KEY), "Signature verification failed"),  # same kid, wrong key
        (make_token(drop=("exp",)), 'missing the "exp" claim'),
        (make_token(drop=("sub",)), 'missing the "sub" claim'),
        (make_token(kid=None), "without a key id"),
        ("not-a-jwt", "Malformed token"),
        # Algorithm confusion: an HS256 token "signed" with the public key must not pass.
        (jwt.encode({"sub": "x"}, HS_SECRET, headers={"kid": KID}), "alg value is not allowed"),
    ],
)  # fmt: skip
async def test_bad_tokens_are_rejected(token: str, message: str) -> None:
    with pytest.raises(AuthError, match=message):
        await make_validator().validate(token)


async def test_keys_are_fetched_once_and_cached() -> None:
    endpoint = FakeJwksEndpoint()
    validator = make_validator(endpoint)
    for _ in range(3):
        await validator.validate(make_token())
    assert endpoint.calls == 1


async def test_concurrent_requests_with_a_new_key_fetch_it_once() -> None:
    endpoint = FakeJwksEndpoint()
    validator = make_validator(endpoint)
    await asyncio.gather(*(validator.validate(make_token()) for _ in range(5)))
    assert endpoint.calls == 1


async def test_key_rotation_refetches_the_keys() -> None:
    endpoint = FakeJwksEndpoint()
    validator = make_validator(endpoint)
    await validator.validate(make_token())
    endpoint.body = jwks(OTHER_KEY, "rotated-key")  # Keycloak rotated its signing key
    claims = await validator.validate(make_token(key=OTHER_KEY, kid="rotated-key"))
    assert claims["sub"] == "8f0c-user"
    assert endpoint.calls == 2


async def test_unknown_key_id_is_rejected_after_one_refetch() -> None:
    endpoint = FakeJwksEndpoint()
    with pytest.raises(AuthError, match="unknown key"):
        await make_validator(endpoint).validate(make_token(kid="nobody"))
    assert endpoint.calls == 1


async def test_keys_without_kid_are_ignored() -> None:
    body = jwks()
    del body["keys"][0]["kid"]
    with pytest.raises(AuthError, match="unknown key"):
        await make_validator(FakeJwksEndpoint(body)).validate(make_token())


@pytest.mark.parametrize(
    ("body", "status"),
    [(None, 503), ("<html>not json</html>", 200), ({"keys": []}, 200)],
)
async def test_identity_provider_problems_are_auth_errors(body: Any, status: int) -> None:
    endpoint = FakeJwksEndpoint(body, status)
    with pytest.raises(AuthError, match="Cannot fetch"):
        await make_validator(endpoint).validate(make_token())


def test_jwks_url_defaults_to_keycloak_and_can_be_overridden() -> None:
    assert CONFIG.jwks_url == f"{CONFIG.issuer}/protocol/openid-connect/certs"
    custom = OidcConfig.from_settings(Settings(oidc_jwks_url="http://idp/keys"))
    assert custom.jwks_url == "http://idp/keys"
    assert isinstance(JwksCache(custom.jwks_url), JwksCache)


@pytest.mark.parametrize(
    ("claims", "expected"),
    [
        ({"preferred_username": "ana@hr", "name": "Ana", "groups": ["/hr", "public"]},
         ("ana@hr", "Ana", ["hr", "public"])),
        ({"preferred_username": "guest"}, ("guest", "guest", [])),
    ],
)  # fmt: skip
def test_user_from_claims(claims: dict[str, Any], expected: tuple[str, str, list[str]]) -> None:
    user = user_from_claims(claims)
    assert (user.id, user.name, user.groups) == expected


@pytest.mark.parametrize(
    ("claims", "message"),
    [
        ({}, "preferred_username"),
        ({"preferred_username": ""}, "preferred_username"),
        ({"preferred_username": "x", "groups": "admins"}, "list of names"),
        ({"preferred_username": "x", "groups": [1]}, "list of names"),
    ],
)
def test_claims_without_a_usable_identity(claims: dict[str, Any], message: str) -> None:
    with pytest.raises(AuthError, match=message):
        user_from_claims(claims)


# ---------- API ----------

DEV_WITHOUT_PLATFORM = {
    "preferred_username": "dev@platform",
    "name": "Dani Vidal",
    "groups": ["public"],
}
USER_SQL = "SELECT name, groups FROM users WHERE id = %s"


def test_me_returns_the_user_from_the_token(client: TestClient) -> None:
    response = client.get("/api/me", headers=bearer("dev@platform"))
    assert response.json() == {
        "id": "dev@platform",
        "name": "Dani Vidal",
        "groups": ["platform", "public"],
    }


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic bGF1cmE6cHc="},
        {"Authorization": f"Bearer {make_token({'aud': 'another-api'})}"},
    ],
)
def test_requests_without_a_valid_token_are_401(
    client: TestClient, headers: dict[str, str]
) -> None:
    response = client.get("/api/access-requests", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_groups_come_from_the_token_not_from_the_database(
    client: TestClient, conn: Conn
) -> None:
    """dev@platform loses the platform group in Keycloak: the next token decides, at once."""
    headers = {"Authorization": f"Bearer {make_token(DEV_WITHOUT_PLATFORM)}"}
    assert client.get("/api/audit", headers=headers).status_code == 403
    row = await (await conn.execute(USER_SQL, ("dev@platform",))).fetchone()
    assert row == {"name": "Dani Vidal", "groups": ["public"]}


async def test_first_login_creates_the_user(client: TestClient, conn: Conn) -> None:
    client.get("/api/me", headers=bearer("nuevo@platform"))
    row = await (await conn.execute(USER_SQL, ("nuevo@platform",))).fetchone()
    assert row == {"name": "nuevo@platform", "groups": ["public"]}
