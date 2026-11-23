"""Append-only audit log: who asked what, which tools ran with which arguments, what came back.

The table itself refuses UPDATE and DELETE (trigger in db/init.sql).
"""

from typing import Any

from psycopg.types.json import Jsonb

from platform_copilot.db import Pool


async def record(
    pool: Pool, user_id: str, conversation_id: str, kind: str, payload: dict[str, Any]
) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO audit_log (user_id, conversation_id, kind, payload)"
            " VALUES (%s, %s, %s, %s)",
            (user_id, conversation_id, kind, Jsonb(payload)),
        )


async def recent(pool: Pool, limit: int) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT id, ts, user_id, conversation_id, kind, payload"
            " FROM audit_log ORDER BY id DESC LIMIT %s",
            (limit,),
        )
        return list(await cursor.fetchall())
