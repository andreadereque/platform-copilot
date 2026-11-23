"""Connection pool. One pool for the whole app, opened and closed with the app's lifespan."""

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


def make_pool(database_url: str) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        database_url,
        min_size=1,
        max_size=5,
        open=False,
        kwargs={"row_factory": dict_row, "autocommit": True},
    )


async def is_db_ready(pool: AsyncConnectionPool) -> bool:
    try:
        async with pool.connection(timeout=2) as conn:
            await conn.execute("SELECT 1")
    except Exception:
        return False
    return True
