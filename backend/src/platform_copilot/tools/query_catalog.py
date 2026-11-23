"""query_catalog: read-only SQL over the data product catalog."""

from typing import Any

import psycopg
from pydantic import BaseModel, Field

from platform_copilot.sql_guard import UnsafeSQLError, guard_sql
from platform_copilot.tools.base import Tool, ToolContext, ToolError, ToolOutput

CATALOG_SOURCE = "catalog:data_products"


class QueryCatalogArgs(BaseModel):
    sql: str = Field(
        min_length=6,
        description=(
            "One PostgreSQL SELECT over the table data_products(name, domain, country, owner,"
            " description, pii). Example: SELECT name, owner FROM data_products WHERE pii"
        ),
    )


async def query_catalog(args: QueryCatalogArgs, ctx: ToolContext) -> ToolOutput:
    try:
        safe_sql = guard_sql(args.sql, ctx.settings.catalog_row_limit)
    except UnsafeSQLError as error:
        raise ToolError(f"Query rejected: {error}") from None
    rows = await _run_read_only(ctx, safe_sql)
    return ToolOutput(
        content={"sql": safe_sql, "row_count": len(rows), "rows": rows},
        summary=f"{len(rows)} filas",
        sources=[CATALOG_SOURCE],
    )


async def _run_read_only(ctx: ToolContext, sql: str) -> list[dict[str, Any]]:
    timeout_ms = int(ctx.settings.catalog_timeout_ms)
    try:
        async with ctx.pool.connection() as conn, conn.transaction():
            await conn.execute("SET TRANSACTION READ ONLY")
            await conn.execute("SET LOCAL ROLE catalog_ro")
            await conn.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
            rows = await (await conn.execute(sql)).fetchall()
    except psycopg.Error as error:
        message = error.diag.message_primary or str(error)
        raise ToolError(f"Query failed: {message}") from None
    return [dict(row) for row in rows]


TOOL = Tool(
    name="query_catalog",
    description=(
        "Query the data product catalog with one read-only SQL SELECT. Table data_products has"
        " columns name, domain, country (ES, FR, DE, IT, EU, GLOBAL), owner (user id),"
        " description and pii (boolean). Use it to find data products, their owners and"
        " whether they contain personal data."
    ),
    args_model=QueryCatalogArgs,
    run=query_catalog,
)
