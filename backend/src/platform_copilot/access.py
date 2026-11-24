"""Access requests: the agent can create them; only the product owner can decide them."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from platform_copilot import audit
from platform_copilot.db import Pool
from platform_copilot.pagination import Key, Page, decode_cursor, make_page

Decision = Literal["approved", "rejected"]


class AccessRequest(BaseModel):
    id: int
    product: str
    owner: str
    requester: str
    reason: str
    status: str
    pii: bool
    created_at: datetime
    decided_by: str | None
    decided_at: datetime | None


class AccessError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


SELECT_REQUESTS = """
SELECT r.id, r.product, p.owner, r.requester, r.reason, r.status, p.pii,
       r.created_at, r.decided_by, r.decided_at
FROM access_requests r JOIN data_products p ON p.name = r.product
"""


async def create_request(pool: Pool, product: str, requester: str, reason: str) -> AccessRequest:
    async with pool.connection() as conn:
        found = await (
            await conn.execute("SELECT owner FROM data_products WHERE name = %s", (product,))
        ).fetchone()
        if found is None:
            raise AccessError(404, f"Data product {product} does not exist")
        if found["owner"] == requester:
            raise AccessError(409, f"{requester} already owns {product}")
        duplicate = await (
            await conn.execute(
                "SELECT id FROM access_requests"
                " WHERE product = %s AND requester = %s AND status = 'pending'",
                (product, requester),
            )
        ).fetchone()
        if duplicate is not None:
            raise AccessError(409, f"There is already a pending request #{duplicate['id']}")
        row = await (
            await conn.execute(
                "INSERT INTO access_requests (product, requester, reason)"
                " VALUES (%s, %s, %s) RETURNING id",
                (product, requester, reason),
            )
        ).fetchone()
    assert row is not None
    return await get_request(pool, int(row["id"]))


async def get_request(pool: Pool, request_id: int) -> AccessRequest:
    async with pool.connection() as conn:
        row = await (
            await conn.execute(SELECT_REQUESTS + " WHERE r.id = %s", (request_id,))
        ).fetchone()
    if row is None:
        raise AccessError(404, f"Access request #{request_id} does not exist")
    return AccessRequest(**row)


async def requests_for_owner(
    pool: Pool, owner: str, limit: int, cursor: str | None = None
) -> Page[AccessRequest]:
    """Requests for the products this user owns, newest first, one keyset page at a time."""
    query = SELECT_REQUESTS + " WHERE p.owner = %(owner)s"
    params: dict[str, object] = {"owner": owner, "limit": limit + 1}
    if cursor is not None:
        key = decode_cursor(cursor)
        query += " AND (r.created_at, r.id) < (%(created_at)s, %(id)s)"
        params.update(created_at=key.created_at, id=key.id)
    query += " ORDER BY r.created_at DESC, r.id DESC LIMIT %(limit)s"
    async with pool.connection() as conn:
        rows = await (await conn.execute(query, params)).fetchall()
    requests = [AccessRequest(**row) for row in rows]
    keys = [Key(request.created_at, request.id) for request in requests]
    return make_page(requests, keys, limit)


async def decide(pool: Pool, request_id: int, decider: str, decision: Decision) -> AccessRequest:
    request = await get_request(pool, request_id)
    if request.status != "pending":
        raise AccessError(409, f"Request #{request_id} is already {request.status}")
    if decider == request.requester:
        raise AccessError(403, "Nobody can decide on their own request")
    if decider != request.owner:
        raise AccessError(403, f"Only the owner of {request.product} can decide")
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE access_requests SET status = %s, decided_by = %s, decided_at = now()"
            " WHERE id = %s",
            (decision, decider, request_id),
        )
    await audit.record(
        pool,
        decider,
        f"request-{request_id}",
        "decision",
        {"request_id": request_id, "status": decision},
    )
    return await get_request(pool, request_id)
