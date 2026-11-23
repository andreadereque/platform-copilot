from collections.abc import AsyncIterator

import psycopg
import pytest

from platform_copilot.config import Settings
from platform_copilot.db import Pool, make_pool
from platform_copilot.embeddings import FakeEmbedder
from platform_copilot.sql_guard import UnsafeSQLError, guard_sql
from platform_copilot.tools.base import ToolContext, ToolError
from platform_copilot.tools.query_catalog import TOOL, QueryCatalogArgs
from platform_copilot.users import User

Conn = psycopg.AsyncConnection[dict[str, object]]
LAURA = User("laura@sales", "Laura Gil", ["sales", "public"])


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT name FROM data_products", "SELECT name FROM data_products LIMIT 50"),
        ("select name from data_products limit 5", "SELECT name FROM data_products LIMIT 5"),
        ("SELECT name FROM data_products LIMIT 999", "SELECT name FROM data_products LIMIT 50"),
        (
            "SELECT count(*) FROM data_products WHERE pii",
            "SELECT COUNT(*) FROM data_products WHERE pii LIMIT 50",
        ),
        ("SELECT 1", "SELECT 1 LIMIT 50"),
    ],
)
def test_safe_queries_get_a_limit(sql: str, expected: str) -> None:
    assert guard_sql(sql, 50) == expected


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("DROP TABLE data_products", "Only SELECT"),
        ("DELETE FROM data_products", "Only SELECT"),
        ("UPDATE data_products SET pii = false", "Only SELECT"),
        ("INSERT INTO data_products (name) VALUES ('x')", "Only SELECT"),
        ("SELECT 1; DROP TABLE data_products", "exactly one"),
        ("", "exactly one"),
        ("SELECT * FROM users", "Only these tables"),
        ("SELECT * FROM data_products JOIN users ON owner = users.id", "Only these tables"),
        ("SELECT name FROM data_products UNION SELECT id FROM users", "Only SELECT"),
        ("SELECT * INTO copy FROM data_products", "INTO"),
        ("WITH x AS (SELECT 1) SELECT * FROM x", "WITH"),
        ("SELECT * FROM data_products FOR UPDATE", "LOCKS"),
        ("SELECT pg_sleep(10)", "pg_sleep"),
        ("SELECT pg_read_file('/etc/passwd')", "pg_read_file"),
        ("SELECT set_config('role', 'postgres', false)", "set_config"),
        ("SELEC name FRM data_products ((", "could not be parsed"),
    ],
)
def test_unsafe_queries_are_rejected(sql: str, reason: str) -> None:
    with pytest.raises(UnsafeSQLError, match=reason):
        guard_sql(sql, 50)


@pytest.fixture
async def ctx(conn: Conn, settings: Settings) -> AsyncIterator[ToolContext]:
    pool: Pool = make_pool(settings.database_url)
    await pool.open()
    yield ToolContext(pool, LAURA, FakeEmbedder(), settings, "c1")
    await pool.close()


async def test_query_catalog_returns_rows(ctx: ToolContext) -> None:
    args = QueryCatalogArgs(
        sql="SELECT name, owner, pii FROM data_products WHERE name LIKE 'sales_es%'"
    )
    output = await TOOL.run(args, ctx)
    assert output.content["row_count"] == 2
    assert {"name": "sales_es_monthly", "owner": "marta@finance", "pii": True} in output.content[
        "rows"
    ]
    assert output.content["sql"].endswith("LIMIT 50")
    assert output.summary == "2 filas"
    assert output.sources == ["catalog:data_products"]


async def test_unsafe_sql_becomes_a_tool_error(ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="Query rejected: Only SELECT"):
        await TOOL.run(QueryCatalogArgs(sql="DELETE FROM data_products"), ctx)


async def test_database_errors_become_tool_errors(ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match='column "missing" does not exist'):
        await TOOL.run(QueryCatalogArgs(sql="SELECT missing FROM data_products"), ctx)


async def test_slow_queries_are_cancelled(conn: Conn, settings: Settings) -> None:
    slow = settings.model_copy(update={"catalog_timeout_ms": 1})
    pool: Pool = make_pool(slow.database_url)
    await pool.open()
    ctx = ToolContext(pool, LAURA, FakeEmbedder(), slow, "c1")
    sql = "SELECT count(*) FROM data_products a, data_products b, data_products c, data_products d"
    with pytest.raises(ToolError, match="statement timeout"):
        await TOOL.run(QueryCatalogArgs(sql=sql), ctx)
    await pool.close()


async def test_the_query_runs_read_only_as_catalog_ro(ctx: ToolContext) -> None:
    output = await TOOL.run(QueryCatalogArgs(sql="SELECT current_user AS who"), ctx)
    assert output.content["rows"] == [{"who": "catalog_ro"}]
