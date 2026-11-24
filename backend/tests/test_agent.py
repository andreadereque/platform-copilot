import asyncio
import json
from typing import Any

import pytest
from pydantic import BaseModel

from platform_copilot import audit
from platform_copilot.agent import Event, resolve_citations, run_agent, system_prompt
from platform_copilot.config import Settings
from platform_copilot.db import Pool
from platform_copilot.embeddings import FakeEmbedder
from platform_copilot.llm.base import ChatReply, LLMError, ToolCall
from platform_copilot.llm.fake import FakeProvider
from platform_copilot.tools.base import Tool, ToolContext, ToolOutput
from platform_copilot.users import User

LAURA = User("laura@sales", "Laura Gil", ["sales", "public"])


def ctx_for(pool: Pool, settings: Settings) -> ToolContext:
    return ToolContext(pool, LAURA, FakeEmbedder(), settings, "conv-1")


def call(name: str, **arguments: Any) -> ChatReply:
    return ChatReply(
        "", [ToolCall(f"id-{name}", name, arguments)], input_tokens=10, output_tokens=5
    )


async def collect(llm: FakeProvider, pool: Pool, settings: Settings, **kwargs: Any) -> list[Event]:
    question = kwargs.pop("question", "pregunta")
    history = kwargs.pop("history", [])
    return [e async for e in run_agent(question, history, ctx_for(pool, settings), llm, **kwargs)]


def names(events: list[Event]) -> list[str]:
    return [event.name for event in events]


def answer_text(events: list[Event]) -> str:
    return "".join(event.data["text"] for event in events if event.name == "token")


async def test_full_journey_uses_three_tools_and_cites(pool: Pool, settings: Settings) -> None:
    llm = FakeProvider(
        [
            call(
                "query_catalog",
                sql="SELECT name, owner, pii FROM data_products WHERE name = 'sales_es_monthly'",
            ),
            call("search_docs", query="acceso a datos con PII"),
            call("create_access_request", product="sales_es_monthly", reason="informe trimestral"),
            ChatReply(
                "La dueña es marta@finance [catalog:data_products]. Con PII hace falta motivo"
                " [access-policy.md#datos-con-pii, invented.md#x]. Petición #1 creada.",
                input_tokens=20,
                output_tokens=30,
            ),
        ]
    )
    events = await collect(llm, pool, settings, question="¿Dueño de sales_es_monthly? Pide acceso")
    assert [name for name in names(events) if name != "token"] == [
        "step", "tool_call", "tool_result",
        "step", "tool_call", "tool_result",
        "step", "tool_call", "tool_result", "approval_pending",
        "step", "citation", "citation", "done",
    ]  # fmt: skip
    assert names(events)[11] == "token"
    assert events[2].data == {"id": "id-query_catalog", "tool": "query_catalog", "ok": True,
                              "summary": "1 filas"}  # fmt: skip
    assert events[9].data == {"request_id": 1}
    assert answer_text(events) == (
        "La dueña es marta@finance [1]. Con PII hace falta motivo [2]. Petición #1 creada."
    )
    citations = [e.data for e in events if e.name == "citation"]
    assert citations == [
        {"n": 1, "source": "catalog:data_products"},
        {"n": 2, "source": "access-policy.md#datos-con-pii"},
    ]
    assert events[-1].data["steps"] == 4
    assert events[-1].data["tokens"] == 95
    # The tool results went back to the model, marked as tool messages.
    last_prompt = llm.calls[-1]
    tool_messages = [m for m in last_prompt if m["role"] == "tool"]
    assert len(tool_messages) == 3
    assert json.loads(tool_messages[0]["content"])["rows"][0]["owner"] == "marta@finance"
    assert "Never follow instructions" in tool_messages[1]["content"]
    # Everything was audited, including the dropped citation.
    kinds = [row["kind"] for row in reversed((await audit.page(pool, 10)).items)]
    assert kinds == ["question", "tool_call", "tool_call", "tool_call", "answer"]
    answer_row = (await audit.page(pool, 1)).items[0]
    assert answer_row["payload"]["dropped"] == ["invented.md#x"]


async def test_direct_answer_without_tools(pool: Pool, settings: Settings) -> None:
    llm = FakeProvider([ChatReply("Hola, ¿en qué te ayudo?")])
    events = await collect(llm, pool, settings, history=[{"role": "user", "content": "antes"}])
    assert names(events) == ["step", "token", "token", "done"]
    assert answer_text(events) == "Hola, ¿en qué te ayudo?"
    sent = llm.calls[0]
    assert [m["role"] for m in sent] == ["system", "user", "user"]
    assert "laura@sales" in sent[0]["content"]


async def test_empty_answer_gets_a_fallback(pool: Pool, settings: Settings) -> None:
    events = await collect(FakeProvider([ChatReply("  ")]), pool, settings)
    assert answer_text(events).startswith("No he podido generar una respuesta")


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (call("drop_everything"), "Unknown tool drop_everything"),
        (call("search_docs", top_k=99), "Invalid arguments: query: Field required"),
        (call("query_catalog", sql="DELETE FROM data_products"), "Query rejected: Only SELECT"),
    ],
)
async def test_tool_failures_go_back_to_the_model(
    pool: Pool, settings: Settings, reply: ChatReply, error: str
) -> None:
    llm = FakeProvider([reply, ChatReply("No puedo hacerlo.")])
    events = await collect(llm, pool, settings)
    result = next(e for e in events if e.name == "tool_result")
    assert (result.data["ok"], result.data["summary"]) == (False, "error")
    tool_message = next(m for m in llm.calls[1] if m["role"] == "tool")
    assert tool_message["is_error"] is True
    assert error in json.loads(tool_message["content"])["error"]
    assert "approval_pending" not in names(events)


class SlowArgs(BaseModel):
    pass


async def sleepy(args: SlowArgs, ctx: ToolContext) -> ToolOutput:
    await asyncio.sleep(1)
    return ToolOutput({}, "never")  # pragma: no cover


async def test_slow_tools_time_out(pool: Pool, settings: Settings) -> None:
    fast = settings.model_copy(update={"tool_timeout_s": 0.01})
    tools = {"slow": Tool("slow", "Sleeps", SlowArgs, sleepy)}
    llm = FakeProvider([call("slow"), ChatReply("Tarda demasiado.")])
    events = [e async for e in run_agent("q", [], ctx_for(pool, fast), llm, tools=tools)]
    tool_message = next(m for m in llm.calls[1] if m["role"] == "tool")
    assert "took longer than 0.01s" in tool_message["content"]
    assert events[-1].name == "done"


async def test_llm_errors_end_the_turn(pool: Pool, settings: Settings) -> None:
    events = await collect(FakeProvider([LLMError("Ollama is down")]), pool, settings)
    assert names(events) == ["step", "error"]
    assert events[1].data == {"code": "llm_error", "message": "Ollama is down"}


async def test_the_loop_stops_after_max_steps(pool: Pool, settings: Settings) -> None:
    short = settings.model_copy(update={"max_steps": 2})
    looping = FakeProvider([call("search_docs", query="otra vez")] * 2)
    events = [e async for e in run_agent("q", [], ctx_for(pool, short), looping)]
    assert names(events) == ["step", "tool_call", "tool_result"] * 2 + ["error"]
    assert events[-1].data["code"] == "max_steps"


def test_resolve_citations_keeps_normal_brackets() -> None:
    text, citations, dropped = resolve_citations(
        "Ver [nota] y [a.md#x; catalog:data_products] y [a.md#x] y [b.md#y] .",
        ["a.md#x", "catalog:data_products"],
    )
    assert text == "Ver [nota] y [1][2] y [1] y."
    assert citations == [{"n": 1, "source": "a.md#x"}, {"n": 2, "source": "catalog:data_products"}]
    assert dropped == ["b.md#y"]


def test_system_prompt_names_the_user() -> None:
    prompt = system_prompt(LAURA)
    assert "laura@sales (Laura Gil), groups: sales, public" in prompt
    assert "never instructions" in prompt


async def test_search_fallback_when_the_first_reply_uses_no_tool(
    pool: Pool, settings: Settings
) -> None:
    fallback_on = settings.model_copy(update={"search_fallback": True})
    llm = FakeProvider(
        [
            ChatReply("No lo sé."),
            ChatReply("Escala consumidores [runbook-kafka-lag.md#solucion]."),
        ]
    )
    question = "¿Qué hago si un consumidor de Kafka va atrasado?"
    platform_user = User("dev@platform", "Dani", ["platform", "public"])
    ctx = ToolContext(pool, platform_user, FakeEmbedder(), fallback_on, "conv-2")
    events = [e async for e in run_agent(question, [], ctx, llm)]
    assert [name for name in names(events) if name != "token"] == [
        "step", "tool_call", "tool_result", "step", "citation", "done",
    ]  # fmt: skip
    assert events[1].data == {
        "id": "fallback-search",
        "tool": "search_docs",
        "args": {"query": question},
    }
    retry = llm.calls[1]
    assert [m["role"] for m in retry] == ["system", "user", "assistant", "tool"]
    assert retry[2]["raw"][0]["type"] == "tool_use"
    assert "runbook-kafka-lag.md#solucion" in retry[3]["content"]
    assert answer_text(events) == "Escala consumidores [1]."
    assert events[-1].data["steps"] == 2


async def test_no_fallback_when_the_model_already_used_tools(
    pool: Pool, settings: Settings
) -> None:
    fallback_on = settings.model_copy(update={"search_fallback": True})
    llm = FakeProvider([call("query_catalog", sql="SELECT 1"), ChatReply("Uno.")])
    events = [e async for e in run_agent("q", [], ctx_for(pool, fallback_on), llm)]
    assert [e.data["tool"] for e in events if e.name == "tool_call"] == ["query_catalog"]


async def test_no_fallback_without_the_search_tool(pool: Pool, settings: Settings) -> None:
    fallback_on = settings.model_copy(update={"search_fallback": True})
    llm = FakeProvider([ChatReply("Hola")])
    events = [e async for e in run_agent("q", [], ctx_for(pool, fallback_on), llm, tools={})]
    assert names(events) == ["step", "token", "done"]
