"""Request dependencies shared by the routes."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from platform_copilot.auth import AuthError, TokenValidator, user_from_claims
from platform_copilot.db import Pool
from platform_copilot.users import User, upsert_user

bearer = HTTPBearer(auto_error=False, description="OIDC access token from Keycloak")


def get_pool(request: Request) -> Pool:
    pool: Pool = request.app.state.pool
    return pool


async def current_user(
    request: Request,
    pool: Annotated[Pool, Depends(get_pool)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    """`Authorization: Bearer <jwt>` -> validated claims -> User. Anything wrong is a 401."""
    if credentials is None:
        raise unauthorized("Missing bearer token")
    validator: TokenValidator = request.app.state.token_validator
    try:
        user = user_from_claims(await validator.validate(credentials.credentials))
    except AuthError as error:
        raise unauthorized(str(error)) from None
    await upsert_user(pool, user)
    return user


def unauthorized(message: str) -> HTTPException:
    return HTTPException(401, message, headers={"WWW-Authenticate": "Bearer"})


PoolDep = Annotated[Pool, Depends(get_pool)]
UserDep = Annotated[User, Depends(current_user)]
