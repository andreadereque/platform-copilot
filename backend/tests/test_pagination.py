"""Keyset pagination: first page, next page, last page, invalid cursors, ties on created_at."""

import base64
from datetime import UTC, datetime

import psycopg
import pytest
from fastapi.testclient import TestClient
from tokens import bearer

from platform_copilot import audit
from platform_copilot.access import requests_for_owner
from platform_copilot.db import Pool
from platform_copilot.pagination import (
    InvalidCursorError,
    Key,
    decode_cursor,
    encode_cursor,
)

Conn = psycopg.AsyncConnection[dict[str, object]]
SAME_INSTANT = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)


async def add_requests(conn: Conn, count: int) -> None:
    """`count` requests to products Marta owns. The first three share one created_at, to
    prove the id breaks the tie; the rest are a minute apart."""
    owned = await (
        await conn.execute("SELECT name FROM data_products WHERE owner = 'marta@finance'")
    ).fetchall()
    names = [str(row["name"]) for row in owned]
    for n in range(count):
        created = SAME_INSTANT
        if n >= 3:
            created = SAME_INSTANT.replace(minute=n)
        await conn.execute(
            "INSERT INTO access_requests (product, requester, reason, created_at)"
            " VALUES (%s, 'laura@sales', %s, %s)",
            (names[n % len(names)], f"motivo {n + 1}", created),
        )


def test_cursor_round_trip_is_url_safe() -> None:
    key = Key(datetime(2026, 11, 24, 10, 30, 0, 123456, tzinfo=UTC), 42)
    cursor = encode_cursor(key)
    assert "=" not in cursor
    assert "/" not in cursor
    assert decode_cursor(cursor) == key


@pytest.mark.parametrize(
    "cursor",
    [
        "%%%",  # not base64
        base64.urlsafe_b64encode(b"not json").decode(),
        base64.urlsafe_b64encode(b'{"a": 1}').decode(),  # not a pair
        base64.urlsafe_b64encode(b'["yesterday", 1]').decode(),  # not a date
        base64.urlsafe_b64encode(b'["2026-11-24T10:00:00+00:00", "x"]').decode(),
        base64.urlsafe_b64encode(b'["2026-11-24T10:00:00", 1]').decode(),  # no time zone
        base64.urlsafe_b64encode(b"[1, 2]").decode(),
    ],
)
def test_invalid_cursors_are_rejected(cursor: str) -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor(cursor)


async def test_access_requests_walk_every_page_once(pool: Pool, conn: Conn) -> None:
    await add_requests(conn, 7)
    first = await requests_for_owner(pool, "marta@finance", 3)
    # Newest first: 7, 6, 5, 4 a minute apart, then 1, 2, 3 at the same instant (id DESC).
    assert [r.id for r in first.items] == [7, 6, 5]
    assert first.next_cursor is not None
    second = await requests_for_owner(pool, "marta@finance", 3, first.next_cursor)
    assert [r.id for r in second.items] == [4, 3, 2]
    assert second.next_cursor is not None
    last = await requests_for_owner(pool, "marta@finance", 3, second.next_cursor)
    assert [r.id for r in last.items] == [1]
    assert last.next_cursor is None


async def test_exact_multiple_of_the_page_size_has_no_empty_last_page(
    pool: Pool, conn: Conn
) -> None:
    await add_requests(conn, 4)
    first = await requests_for_owner(pool, "marta@finance", 2)
    second = await requests_for_owner(pool, "marta@finance", 2, first.next_cursor)
    assert [r.id for r in second.items] == [2, 1]
    assert second.next_cursor is None


async def test_new_rows_do_not_shift_the_next_page(pool: Pool, conn: Conn) -> None:
    """The weakness of OFFSET: a row inserted while paging would repeat one on page 2."""
    await add_requests(conn, 4)
    first = await requests_for_owner(pool, "marta@finance", 2)
    await add_requests(conn, 1)  # a brand-new request arrives
    second = await requests_for_owner(pool, "marta@finance", 2, first.next_cursor)
    assert [r.id for r in first.items] == [4, 3]
    assert [r.id for r in second.items] == [2, 1]


async def test_audit_pages(pool: Pool) -> None:
    for n in range(5):
        await audit.record(pool, "laura@sales", "c1", "question", {"n": n})
    first = await audit.page(pool, 2)
    assert [row["payload"]["n"] for row in first.items] == [4, 3]
    rest = await audit.page(pool, 10, first.next_cursor)
    assert [row["payload"]["n"] for row in rest.items] == [2, 1, 0]
    assert rest.next_cursor is None


# ---------- API ----------

MARTA = bearer("marta@finance")
DEV = bearer("dev@platform")


async def test_api_access_requests_pages(client: TestClient, conn: Conn) -> None:
    await add_requests(conn, 3)
    first = client.get("/api/access-requests?limit=2", headers=MARTA).json()
    assert [item["id"] for item in first["items"]] == [3, 2]
    cursor = first["next_cursor"]
    last = client.get(f"/api/access-requests?limit=2&cursor={cursor}", headers=MARTA).json()
    assert [item["id"] for item in last["items"]] == [1]
    assert last["next_cursor"] is None


async def test_api_audit_pages(client: TestClient, pool: Pool) -> None:
    for n in range(3):
        await audit.record(pool, "laura@sales", "c1", "question", {"n": n})
    first = client.get("/api/audit?limit=2", headers=DEV).json()
    last = client.get(f"/api/audit?limit=2&cursor={first['next_cursor']}", headers=DEV).json()
    assert [row["payload"]["n"] for row in first["items"] + last["items"]] == [2, 1, 0]


@pytest.mark.parametrize(
    ("url", "headers"),
    [("/api/access-requests?cursor=nope", MARTA), ("/api/audit?cursor=nope", DEV)],
)
def test_api_invalid_cursor_is_400(client: TestClient, url: str, headers: dict[str, str]) -> None:
    response = client.get(url, headers=headers)
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid cursor"}


@pytest.mark.parametrize("limit", [0, 101])
def test_api_limit_is_bounded(client: TestClient, limit: int) -> None:
    response = client.get(f"/api/access-requests?limit={limit}", headers=MARTA)
    assert response.status_code == 422
