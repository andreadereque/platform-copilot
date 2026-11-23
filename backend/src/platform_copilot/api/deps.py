"""Request dependencies shared by the routes."""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from platform_copilot.db import Pool
from platform_copilot.users import UnknownUserError, User, get_user


def get_pool(request: Request) -> Pool:
    pool: Pool = request.app.state.pool
    return pool


async def current_user(
    pool: Annotated[Pool, Depends(get_pool)],
    x_user_id: Annotated[str, Header(description="Practice stand-in for an SSO identity")],
) -> User:
    try:
        return await get_user(pool, x_user_id)
    except UnknownUserError:
        raise HTTPException(401, f"Unknown user {x_user_id}") from None


PoolDep = Annotated[Pool, Depends(get_pool)]
UserDep = Annotated[User, Depends(current_user)]
