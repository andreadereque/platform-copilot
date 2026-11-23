"""The fake users of this practice project. In production: SSO (OIDC) and the company directory."""

from dataclasses import dataclass

from platform_copilot.db import Pool


@dataclass(frozen=True)
class User:
    id: str
    name: str
    groups: list[str]


class UnknownUserError(Exception):
    pass


async def list_users(pool: Pool) -> list[User]:
    async with pool.connection() as conn:
        rows = await (
            await conn.execute("SELECT id, name, groups FROM users ORDER BY id")
        ).fetchall()
    return [User(**row) for row in rows]


async def get_user(pool: Pool, user_id: str) -> User:
    async with pool.connection() as conn:
        cursor = await conn.execute("SELECT id, name, groups FROM users WHERE id = %s", (user_id,))
        row = await cursor.fetchone()
    if row is None:
        raise UnknownUserError(user_id)
    return User(**row)
