"""Feedback from users and the monitoring report for the platform team."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from platform_copilot import feedback, monitoring
from platform_copilot.api.deps import PoolDep, UserDep
from platform_copilot.feedback import FeedbackIn

router = APIRouter(prefix="/api")


@router.post("/feedback", status_code=201)
async def give_feedback(body: FeedbackIn, pool: PoolDep, user: UserDep) -> dict[str, int]:
    """Thumbs up (1) or down (-1) on the last answer of a conversation."""
    return {"id": await feedback.record(pool, user.id, body)}


@router.get("/monitoring")
async def monitoring_report(
    pool: PoolDep, user: UserDep, days: int = Query(7, ge=1, le=90)
) -> dict[str, Any]:
    """Platform team only: health and drift, last `days` against the `days` before."""
    if "platform" not in user.groups:
        raise HTTPException(403, "Only the platform team can read the monitoring report")
    return await monitoring.drift_report(pool, datetime.now(UTC), days)
