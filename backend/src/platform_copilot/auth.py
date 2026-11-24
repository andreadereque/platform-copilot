"""Who is calling: validate the OIDC access token (a JWT) and turn its claims into a User.

The browser logs in with Keycloak (OAuth 2.0 authorization code + PKCE) and sends the access token
as `Authorization: Bearer <jwt>`. This API is a *resource server*: it never sees a password, it
only checks that the token

1. is signed with RS256 by one of the identity provider's public keys (JWKS, fetched by `kid`),
2. was issued by our realm (`iss`) for this API (`aud`),
3. has not expired (`exp`, with a little clock leeway).

Checking is local and offline (no call to Keycloak per request): the public keys are cached and
fetched again only when a token arrives signed with a key id we have not seen (key rotation).
"""

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

from platform_copilot.config import Settings
from platform_copilot.users import User

ALGORITHMS = ["RS256"]  # never accept "none" or HS256: the algorithm is not chosen by the token
LEEWAY_S = 30


class AuthError(Exception):
    """The token is missing, malformed, badly signed, expired or not for us: HTTP 401."""


@dataclass(frozen=True)
class OidcConfig:
    issuer: str
    audience: str
    jwks_url: str

    @classmethod
    def from_settings(cls, settings: Settings) -> "OidcConfig":
        jwks_url = settings.oidc_jwks_url
        if not jwks_url:
            jwks_url = f"{settings.oidc_issuer}/protocol/openid-connect/certs"
        return cls(settings.oidc_issuer, settings.oidc_audience, jwks_url)


class JwksCache:
    """The identity provider's public signing keys, by key id (`kid`)."""

    def __init__(self, jwks_url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._url = jwks_url
        self._transport = transport  # tests pass an httpx.MockTransport: no network
        self._keys: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    async def key_for(self, kid: str) -> Any:
        if kid not in self._keys:
            # One refresh at a time: 100 requests with a new kid cause one fetch, not 100.
            async with self._lock:
                if kid not in self._keys:
                    await self._refresh()
        if kid not in self._keys:
            raise AuthError("Token signed with an unknown key")
        return self._keys[kid]

    async def _refresh(self) -> None:
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=5) as client:
                response = await client.get(self._url)
                response.raise_for_status()
                key_set = jwt.PyJWKSet.from_dict(response.json())
        except (httpx.HTTPError, ValueError, jwt.PyJWKSetError) as error:
            raise AuthError("Cannot fetch the identity provider's keys") from error
        self._keys = {key.key_id: key.key for key in key_set.keys if key.key_id}


class TokenValidator:
    def __init__(self, config: OidcConfig, jwks: JwksCache) -> None:
        self._config = config
        self._jwks = jwks

    async def validate(self, token: str) -> dict[str, Any]:
        """Return the token's claims, or raise AuthError."""
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.InvalidTokenError as error:
            raise AuthError("Malformed token") from error
        if not isinstance(kid, str):
            raise AuthError("Token without a key id")
        key = await self._jwks.key_for(kid)
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=ALGORITHMS,
                audience=self._config.audience,
                issuer=self._config.issuer,
                leeway=LEEWAY_S,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.InvalidTokenError as error:
            raise AuthError(f"Invalid token: {error}") from error
        return claims


def user_from_claims(claims: dict[str, Any]) -> User:
    """Keycloak puts the username in `preferred_username` and, with our group mapper, the
    user's groups in `groups`. Authorization (who sees which docs, who reads the audit log)
    uses these groups, so they come from the identity provider, not from the client."""
    user_id = claims.get("preferred_username")
    if not isinstance(user_id, str) or not user_id:
        raise AuthError("Token without preferred_username")
    groups = claims.get("groups", [])
    if not isinstance(groups, list) or not all(isinstance(g, str) for g in groups):
        raise AuthError("The groups claim must be a list of names")
    name = claims.get("name")
    if not isinstance(name, str) or not name:
        name = user_id
    return User(user_id, name, [group.lstrip("/") for group in groups])
