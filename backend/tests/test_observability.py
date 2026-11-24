"""Traces of an agent run: one agent.run span with the model and tool spans inside it."""

from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from platform_copilot import audit, main, observability
from platform_copilot.agent import run_agent
from platform_copilot.config import Settings
from platform_copilot.db import Pool
from platform_copilot.embeddings import FakeEmbedder
from platform_copilot.llm.base import ChatReply, LLMError, ToolCall
from platform_copilot.llm.fake import FakeProvider
from platform_copilot.observability import Prices, TracedProvider
from platform_copilot.tools.base import ToolContext
from platform_copilot.users import User

LAURA = User("laura@sales", "Laura Gil", ["sales", "public"])
# Made-up prices for the test: 3 USD per million input tokens, 15 per million output tokens.
PRICES = Prices(input_per_mtok=3.0, output_per_mtok=15.0)


def search(query: str) -> ChatReply:
    call = ToolCall("c1", "search_docs", {"query": query})
    return ChatReply("", [call], input_tokens=1000, output_tokens=100)


async def run(pool: Pool, settings: Settings, replies: list[Any]) -> list[Any]:
    llm = TracedProvider(FakeProvider(replies), PRICES)
    ctx = ToolContext(pool, LAURA, FakeEmbedder(), settings, "conv-obs")
    return [event async for event in run_agent("¿guardias?", [], ctx, llm)]


def by_name(spans: InMemorySpanExporter) -> dict[str, Any]:
    return {span.name: span for span in spans.get_finished_spans()}


async def test_one_trace_per_question_with_tokens_cost_and_latency(
    pool: Pool, settings: Settings, spans: InMemorySpanExporter
) -> None:
    final = ChatReply("Mira [oncall-guide.md#x].", input_tokens=2000, output_tokens=200)
    events = await run(pool, settings, [search("guardias"), final])
    found = by_name(spans)
    agent, tool = found["agent.run"], found["tool search_docs"]
    llm_spans = [s for s in spans.get_finished_spans() if s.name == "llm.chat"]
    assert len(llm_spans) == 2
    for child in [*llm_spans, tool]:  # all in the same trace, under agent.run
        assert child.parent.span_id == agent.context.span_id
        assert child.context.trace_id == agent.context.trace_id
    first: dict[str, Any] = dict(llm_spans[0].attributes or {})
    assert first["gen_ai.request.model"] == "fake-model"
    assert (first["gen_ai.usage.input_tokens"], first["gen_ai.usage.output_tokens"]) == (1000, 100)
    assert first["cost_usd"] == pytest.approx(0.0045)  # 1000 * 3 / 1e6 + 100 * 15 / 1e6
    assert first["latency_ms"] >= 0
    assert tool.attributes["ok"] is True
    total = 0.0045 + 0.009
    assert agent.attributes["outcome"] == "answered"
    assert agent.attributes["cost_usd"] == pytest.approx(total)
    assert events[-1].data["cost_usd"] == pytest.approx(total)
    answer = (await audit.page(pool, 1)).items[0]["payload"]
    assert answer["cost_usd"] == pytest.approx(total)
    assert answer["tokens"] == 3300


async def test_model_errors_mark_the_spans(
    pool: Pool, settings: Settings, spans: InMemorySpanExporter
) -> None:
    await run(pool, settings, [LLMError("Ollama is down")])
    found = by_name(spans)
    assert found["llm.chat"].status.status_code == StatusCode.ERROR
    assert found["agent.run"].attributes["outcome"] == "llm_error"
    assert found["agent.run"].status.description == "Ollama is down"


async def test_failed_tools_mark_their_span(
    pool: Pool, settings: Settings, spans: InMemorySpanExporter
) -> None:
    bad = ChatReply("", [ToolCall("c1", "query_catalog", {"sql": "DELETE FROM users"})])
    await run(pool, settings, [bad, ChatReply("No puedo.")])
    tool = by_name(spans)["tool query_catalog"]
    assert tool.attributes["ok"] is False
    assert tool.status.status_code == StatusCode.ERROR


async def test_traced_provider_delegates_readiness() -> None:
    assert await TracedProvider(FakeProvider(), Prices()).is_ready() is True


def test_setup_tracing_installs_a_console_exporter(monkeypatch: pytest.MonkeyPatch) -> None:
    installed: list[Any] = []
    monkeypatch.setattr(trace, "set_tracer_provider", installed.append)
    provider = observability.setup_tracing()
    assert installed == [provider]
    assert provider.resource.attributes["service.name"] == "platform-copilot"


def test_the_app_turns_tracing_on_from_the_settings(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(main, "setup_tracing", lambda: calls.append("on"))
    main.create_app(settings.model_copy(update={"otel_exporter": "console"}))
    assert calls == ["on"]
