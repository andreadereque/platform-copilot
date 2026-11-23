"""Liveness and readiness probes, as Kubernetes/OpenShift expect them."""

from fastapi import APIRouter, Request, Response

from platform_copilot.db import is_db_ready

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process answers. Never checks dependencies.

    A database outage must not make Kubernetes restart every pod.
    """
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request, response: Response) -> dict[str, bool]:
    """Readiness: only send traffic when the database answers."""
    db_ok = await is_db_ready(request.app.state.pool)
    if not db_ok:
        response.status_code = 503
    return {"database": db_ok}
