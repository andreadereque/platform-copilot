"""Users, access requests and the audit log."""

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from platform_copilot import audit
from platform_copilot.access import AccessError, AccessRequest, Decision, decide, requests_for_owner
from platform_copilot.api.deps import PoolDep, UserDep
from platform_copilot.pagination import InvalidCursorError, Page
from platform_copilot.users import User, list_users

router = APIRouter(prefix="/api")


@router.get("/users")
async def users(pool: PoolDep) -> list[User]:
    return await list_users(pool)


@router.get("/access-requests")
async def my_requests_to_decide(
    pool: PoolDep,
    user: UserDep,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, max_length=200),
) -> Page[AccessRequest]:
    """Requests for the data products the current user owns, newest first.

    Pass the response's `next_cursor` back as `cursor` to get the next page."""
    try:
        return await requests_for_owner(pool, user.id, limit, cursor)
    except InvalidCursorError as error:
        raise HTTPException(400, str(error)) from None


@router.post("/access-requests/{request_id}/approve")
async def approve(request_id: int, pool: PoolDep, user: UserDep) -> AccessRequest:
    return await _decide(pool, request_id, user, "approved")


@router.post("/access-requests/{request_id}/reject")
async def reject(request_id: int, pool: PoolDep, user: UserDep) -> AccessRequest:
    return await _decide(pool, request_id, user, "rejected")


async def _decide(pool: PoolDep, request_id: int, user: User, decision: Decision) -> AccessRequest:
    try:
        return await decide(pool, request_id, user.id, decision)
    except AccessError as error:
        raise HTTPException(error.status_code, str(error)) from None


@router.get("/audit")
async def audit_log(
    pool: PoolDep,
    user: UserDep,
    limit: int = Query(50, ge=1, le=500),
    cursor: str | None = Query(None, max_length=200),
) -> Page[dict[str, Any]]:
    """Platform team only: audit events, newest first, one keyset page at a time."""
    if "platform" not in user.groups:
        raise HTTPException(403, "Only the platform team can read the audit log")
    try:
        return await audit.page(pool, limit, cursor)
    except InvalidCursorError as error:
        raise HTTPException(400, str(error)) from None
