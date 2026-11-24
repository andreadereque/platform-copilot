"""The LangGraph version of the agent: routing, human-in-the-loop interrupt, memory per thread."""

from typing import Any

import psycopg
from langgraph.checkpoint.memory import InMemorySaver

from platform_copilot.agent import DEFAULT_TOOLS
from platform_copilot.agent_graph import DECLINED, ask, build_graph, resume
from platform_copilot.config import Settings
from platform_copilot.db import Pool
from platform_copilot.embeddings import FakeEmbedder
from platform_copilot.llm.base import ChatReply, LLMError, ToolCall
from platform_copilot.llm.fake import FakeProvider
from platform_copilot.tools.base import ToolContext
from platform_copilot.users import User

Conn = psycopg.AsyncConnection[dict[str, object]]
LAURA = User("laura@sales", "Laura Gil", ["sales", "public"])
REQUEST = {"product": "sales_es_monthly", "reason": "informe trimestral"}


def ctx_for(pool: Pool, settings: Settings, conversation: str = "thread-1") -> ToolContext:
    return ToolContext(pool, LAURA, FakeEmbedder(), settings, conversation)


def calls(*items: tuple[str, dict[str, Any]]) -> ChatReply:
    return ChatReply("", [ToolCall(f"id-{name}", name, args) for name, args in items])


async def count_requests(conn: Conn) -> int:
    row = await (await conn.execute("SELECT count(*) AS n FROM access_requests")).fetchone()
    assert row is not None
    return int(str(row["n"]))


async def test_read_only_tools_run_without_asking(pool: Pool, settings: Settings) -> None:
    llm = FakeProvider([
        calls(("search_docs", {"query": "guardias"})),
        ChatReply("Mira la guía [how-to-request-access.md#pasos]."),
    ])  # fmt: skip
    ctx = ctx_for(pool, settings)
    result = await ask(build_graph(ctx, llm), ctx, "¿Cómo pido acceso?")
    assert result.pending is None
    assert result.answer == "Mira la guía [1]."
    assert result.citations == [{"n": 1, "source": "how-to-request-access.md#pasos"}]
    tool_message = llm.calls[1][-1]
    assert (tool_message["role"], tool_message["name"]) == ("tool", "search_docs")


async def test_a_write_waits_for_the_user_and_runs_when_approved(
    pool: Pool, settings: Settings, conn: Conn
) -> None:
    llm = FakeProvider([
        calls(("create_access_request", REQUEST)),
        ChatReply("Petición creada; la aprueba marta@finance."),
    ])  # fmt: skip
    ctx = ctx_for(pool, settings)
    graph = build_graph(ctx, llm)
    paused = await ask(graph, ctx, "Pídeme acceso a sales_es_monthly")
    assert paused.pending == {
        "actions": [{"id": "id-create_access_request", "tool": "create_access_request",
                     "args": REQUEST}]
    }  # fmt: skip
    assert await count_requests(conn) == 0  # nothing written while paused
    done = await resume(graph, ctx, approved=True)
    assert done.answer == "Petición creada; la aprueba marta@finance."
    assert await count_requests(conn) == 1


async def test_a_declined_write_never_runs(pool: Pool, settings: Settings, conn: Conn) -> None:
    llm = FakeProvider([
        calls(("create_access_request", REQUEST), ("search_docs", {"query": "acceso"})),
        ChatReply("De acuerdo, no la he creado."),
    ])  # fmt: skip
    ctx = ctx_for(pool, settings)
    graph = build_graph(ctx, llm)
    await ask(graph, ctx, "Pídeme acceso")
    done = await resume(graph, ctx, approved=False)
    assert done.answer == "De acuerdo, no la he creado."
    assert await count_requests(conn) == 0
    results = {m["name"]: m for m in llm.calls[1] if m["role"] == "tool"}
    assert DECLINED in results["create_access_request"]["content"]
    assert results["search_docs"]["is_error"] is False  # the read in the same turn still ran


async def test_declining_the_only_action_goes_back_to_the_model(
    pool: Pool, settings: Settings
) -> None:
    llm = FakeProvider([calls(("create_access_request", REQUEST)), ChatReply("Vale.")])
    ctx = ctx_for(pool, settings)
    graph = build_graph(ctx, llm)
    await ask(graph, ctx, "Pídeme acceso")
    assert (await resume(graph, ctx, approved=False)).answer == "Vale."


async def test_the_checkpointer_remembers_the_conversation(pool: Pool, settings: Settings) -> None:
    llm = FakeProvider([ChatReply("Hola, Laura."), ChatReply("Te llamas Laura.")])
    ctx = ctx_for(pool, settings)
    graph = build_graph(ctx, llm, DEFAULT_TOOLS, InMemorySaver())  # PostgresSaver in production
    await ask(graph, ctx, "Hola")
    await ask(graph, ctx, "¿Cómo me llamo?")
    second_turn = llm.calls[1]
    assert [m["role"] for m in second_turn] == ["system", "user", "assistant", "user"]
    other = ctx_for(pool, settings, "thread-2")  # another thread starts empty
    llm.replies = [ChatReply("¿Quién eres?")]
    await ask(graph, other, "¿Cómo me llamo?")
    assert [m["role"] for m in llm.calls[2]] == ["system", "user"]


async def test_too_many_steps_stops_with_an_error(pool: Pool, settings: Settings) -> None:
    loop: list[ChatReply | LLMError] = [calls(("search_docs", {"query": "x"}))] * 5
    ctx = ctx_for(pool, settings.model_copy(update={"max_steps": 2}))
    result = await ask(build_graph(ctx, FakeProvider(loop)), ctx, "?")
    assert result.error == "Stopped after 2 steps without a final answer"
