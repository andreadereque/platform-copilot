"""Check the SQL the model writes before it touches the database.

Layer one of three. Even if this check had a hole, the query still runs in a READ ONLY
transaction, as the catalog_ro role (SELECT on data_products only), with a statement timeout.
"""

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

ALLOWED_TABLES = {"data_products"}
BLOCKED_FUNCTION_PREFIXES = ("pg_", "set_config", "dblink", "lo_", "current_setting")


class UnsafeSQLError(ValueError):
    pass


def guard_sql(sql: str, row_limit: int) -> str:
    """Return a safe version of `sql` (with a LIMIT) or raise UnsafeSQLError saying why."""
    statement = _single_statement(sql)
    if not isinstance(statement, exp.Select):
        raise UnsafeSQLError("Only SELECT queries are allowed")
    for clause, label in (("with_", "WITH"), ("into", "INTO"), ("locks", "LOCKS")):
        if statement.args.get(clause):
            raise UnsafeSQLError(f"{label} is not allowed")
    tables = {table.name.lower() for table in statement.find_all(exp.Table)}
    if not tables <= ALLOWED_TABLES:
        raise UnsafeSQLError(
            f"Only these tables can be queried: {', '.join(sorted(ALLOWED_TABLES))}"
        )
    for function in statement.find_all(exp.Func):
        name = function.sql_name().lower()
        if isinstance(function, exp.Anonymous):
            name = function.name.lower()
        if name.startswith(BLOCKED_FUNCTION_PREFIXES):
            raise UnsafeSQLError(f"Function {name} is not allowed")
    return _with_limit(statement, row_limit).sql(dialect="postgres")


def _single_statement(sql: str) -> exp.Expr:
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except ParseError:
        raise UnsafeSQLError("The SQL could not be parsed") from None
    if len(statements) != 1:
        raise UnsafeSQLError("Send exactly one SQL statement")
    return statements[0]


def _with_limit(statement: exp.Select, row_limit: int) -> exp.Select:
    limit = statement.args.get("limit")
    if limit is not None:
        value = limit.expression
        if isinstance(value, exp.Literal) and value.is_int and int(value.this) <= row_limit:
            return statement
    return statement.limit(row_limit)
