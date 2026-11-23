"""POST /api/chat: run the agent and stream its events as Server-Sent Events."""

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from platform_copilot.agent import Event, run_agent
from platform_copilot.api.deps import PoolDep, UserDep
from platform_copilot.tools.base import ToolContext

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=64)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=10)


@router.post("/chat", response_class=StreamingResponse)
async def chat(
    body: ChatRequest, request: Request, pool: PoolDep, user: UserDep
) -> StreamingResponse:
    conversation_id = body.conversation_id or uuid.uuid4().hex[:12]
    state = request.app.state
    ctx = ToolContext(pool, user, state.embedder, state.settings, conversation_id)
    history = [turn.model_dump() for turn in body.history]
    events = run_agent(body.message, history, ctx, state.llm)
    return StreamingResponse(
        sse(events, conversation_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def sse(events: AsyncIterator[Event], conversation_id: str) -> AsyncIterator[str]:
    yield format_event(Event("start", {"conversation_id": conversation_id}))
    try:
        async for event in events:
            yield format_event(event)
    except Exception:
        logger.exception("agent_failed", extra={"conversation_id": conversation_id})
        yield format_event(Event("error", {"code": "internal", "message": "Unexpected error"}))


def format_event(event: Event) -> str:
    data = json.dumps(event.data, ensure_ascii=False, default=str)
    return f"event: {event.name}\ndata: {data}\n\n"
