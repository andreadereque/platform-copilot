"""Append-only audit log: who asked what, which tools ran with which arguments, what came back.

The table itself refuses UPDATE and DELETE (trigger in db/init.sql).
"""

from typing import Any

from psycopg.types.json import Jsonb

from platform_copilot.db import Pool
from platform_copilot.pagination import Key, Page, decode_cursor, make_page


async def record(
    pool: Pool, user_id: str, conversation_id: str, kind: str, payload: dict[str, Any]
) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO audit_log (user_id, conversation_id, kind, payload)"
            " VALUES (%s, %s, %s, %s)",
            (user_id, conversation_id, kind, Jsonb(payload)),
        )


async def page(pool: Pool, limit: int, cursor: str | None = None) -> Page[dict[str, Any]]:
    """Audit events, newest first, one keyset page at a time (see pagination.py)."""
    query = "SELECT id, ts, user_id, conversation_id, kind, payload FROM audit_log"
    params: dict[str, object] = {"limit": limit + 1}
    if cursor is not None:
        key = decode_cursor(cursor)
        query += " WHERE (ts, id) < (%(ts)s, %(id)s)"
        params.update(ts=key.created_at, id=key.id)
    query += " ORDER BY ts DESC, id DESC LIMIT %(limit)s"
    async with pool.connection() as conn:
        rows = list(await (await conn.execute(query, params)).fetchall())
    keys = [Key(row["ts"], row["id"]) for row in rows]
    return make_page(rows, keys, limit)
