"""Connection pool. One pool for the whole app, opened and closed with the app's lifespan."""

from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

Pool = AsyncConnectionPool[AsyncConnection[DictRow]]


def make_pool(database_url: str) -> Pool:
    return Pool(
        database_url,
        min_size=1,
        max_size=5,
        open=False,
        connection_class=AsyncConnection[DictRow],
        kwargs={"row_factory": dict_row, "autocommit": True},
    )


async def is_db_ready(pool: Pool) -> bool:
    try:
        async with pool.connection(timeout=2) as conn:
            await conn.execute("SELECT 1")
    except Exception:
        return False
    return True
