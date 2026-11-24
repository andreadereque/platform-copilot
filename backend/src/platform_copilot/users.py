"""Users come from the identity provider (Keycloak); the users table mirrors them.

The table exists so access requests and data products can reference users with a foreign key.
It is filled on login ("just-in-time provisioning") and by the seed for the fake owners.
"""

from dataclasses import dataclass

from platform_copilot.db import Pool


@dataclass(frozen=True)
class User:
    id: str
    name: str
    groups: list[str]


class UnknownUserError(Exception):
    pass


async def get_user(pool: Pool, user_id: str) -> User:
    async with pool.connection() as conn:
        cursor = await conn.execute("SELECT id, name, groups FROM users WHERE id = %s", (user_id,))
        row = await cursor.fetchone()
    if row is None:
        raise UnknownUserError(user_id)
    return User(**row)


UPSERT_USER = """
INSERT INTO users (id, name, groups) VALUES (%(id)s, %(name)s, %(groups)s)
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, groups = EXCLUDED.groups
WHERE (users.name, users.groups) IS DISTINCT FROM (EXCLUDED.name, EXCLUDED.groups)
"""


async def upsert_user(pool: Pool, user: User) -> None:
    """Mirror the token's user. Writes only when the name or the groups changed."""
    async with pool.connection() as conn:
        await conn.execute(UPSERT_USER, {"id": user.id, "name": user.name, "groups": user.groups})
