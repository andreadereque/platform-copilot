from collections.abc import AsyncIterator, Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from platform_copilot.agent import Event
from platform_copilot.api.chat import format_event, sse
from platform_copilot.config import Settings
from platform_copilot.db import Pool
from platform_copilot.embeddings import FakeEmbedder
from platform_copilot.llm.base import ChatReply, ToolCall
from platform_copilot.llm.fake import FakeProvider
from platform_copilot.main import create_app

Conn = psycopg.AsyncConnection[dict[str, object]]
LAURA = {"X-User-Id": "laura@sales"}


def parse_sse(text: str) -> list[tuple[str, str]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = block.split("\n")
        events.append((lines[0].removeprefix("event: "), lines[1].removeprefix("data: ")))
    return events


@pytest.fixture
def llm() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def chat_client(settings: Settings, pool: Pool, llm: FakeProvider) -> Iterator[TestClient]:
    with TestClient(create_app(settings, llm=llm, embedder=FakeEmbedder())) as client:
        yield client


def test_chat_streams_the_agent_events(chat_client: TestClient, llm: FakeProvider) -> None:
    llm.replies = [
        ChatReply("", [ToolCall("c1", "search_docs", {"query": "guardias"})]),
        ChatReply("Mira la política [how-to-request-access.md#pasos]."),
    ]
    response = chat_client.post(
        "/api/chat",
        headers=LAURA,
        json={"message": "¿Cómo pido acceso?", "conversation_id": "abc",
              "history": [{"role": "user", "content": "hola"}]},
    )  # fmt: skip
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    assert events[0] == ("start", '{"conversation_id": "abc"}')
    assert [name for name, _ in events][1:4] == ["step", "tool_call", "tool_result"]
    assert events[-1][0] == "done"
    assert llm.calls[0][1] == {"role": "user", "content": "hola"}


def test_chat_creates_a_conversation_id(chat_client: TestClient, llm: FakeProvider) -> None:
    llm.replies = [ChatReply("Hola")]
    events = parse_sse(chat_client.post("/api/chat", headers=LAURA, json={"message": "hola"}).text)
    assert len(events[0][1]) > len('{"conversation_id": ""}')


def test_chat_validates_the_request(chat_client: TestClient) -> None:
    assert chat_client.post("/api/chat", headers=LAURA, json={"message": ""}).status_code == 422
    unknown = chat_client.post("/api/chat", headers={"X-User-Id": "x"}, json={"message": "hola"})
    assert unknown.status_code == 401


async def test_sse_turns_crashes_into_an_error_event() -> None:
    async def broken() -> AsyncIterator[Event]:
        yield Event("step", {"n": 1})
        raise RuntimeError("database exploded")

    chunks: list[Any] = [chunk async for chunk in sse(broken(), "c1")]
    assert chunks[-1] == format_event(
        Event("error", {"code": "internal", "message": "Unexpected error"})
    )
    assert "database exploded" not in "".join(chunks)


def test_format_event_keeps_accents() -> None:
    assert format_event(Event("token", {"text": "Política"})) == (
        'event: token\ndata: {"text": "Política"}\n\n'
    )
