"""Tracing, latency and cost of every model call, tool call and agent run (OpenTelemetry).

One trace per question:

    agent.run  (user, conversation, prompt version, steps, tokens, cost, outcome)
    ├── llm.chat     (model, input/output tokens, latency, cost, tool calls asked)
    ├── tool search_docs   (ok, latency)
    ├── llm.chat
    └── ...

Attribute names follow the OpenTelemetry GenAI semantic conventions (`gen_ai.*`), so any
backend that understands them (Cloud Trace, Grafana Tempo, Langfuse, Datadog) shows tokens and
models without extra mapping. Locally the exporter is the console or nothing; in production it
would be OTLP to the company's collector.

Cost is tokens times price. Prices come from the settings (USD per million tokens), because they
change and differ per contract: a local model costs 0, an API model what the price list says.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace

from opentelemetry import context, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.trace import Status, StatusCode

from platform_copilot.llm.base import ChatReply, LLMError, LLMProvider, Message, ToolSchema

tracer = trace.get_tracer("platform_copilot")


@dataclass(frozen=True)
class Prices:
    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.input_per_mtok + output_tokens * self.output_per_mtok
        ) / 1_000_000


class TracedProvider:
    """Wraps any LLMProvider: one span per call with tokens, latency and cost. The agent does
    not know it is being traced (decorator pattern), and neither do the providers."""

    def __init__(self, inner: LLMProvider, prices: Prices) -> None:
        self.inner = inner
        self.prices = prices
        self.name = inner.name
        self.model = inner.model

    async def chat(self, messages: list[Message], tools: list[ToolSchema]) -> ChatReply:
        with tracer.start_as_current_span("llm.chat") as span:
            span.set_attribute("gen_ai.system", self.name)
            span.set_attribute("gen_ai.request.model", self.model)
            span.set_attribute("gen_ai.request.messages", len(messages))
            started = time.perf_counter()
            try:
                reply = await self.inner.chat(messages, tools)
            except LLMError as error:
                span.set_status(Status(StatusCode.ERROR, str(error)))
                raise
            finally:
                span.set_attribute("latency_ms", elapsed_ms(started))
            cost = self.prices.cost(reply.input_tokens, reply.output_tokens)
            span.set_attribute("gen_ai.usage.input_tokens", reply.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", reply.output_tokens)
            span.set_attribute("gen_ai.response.tool_calls", len(reply.tool_calls))
            span.set_attribute("cost_usd", cost)
            return replace(reply, cost_usd=cost)

    async def is_ready(self) -> bool:
        return await self.inner.is_ready()


@contextmanager
def child_of(span: trace.Span) -> Iterator[None]:
    """Make `span` the parent of the spans created inside the block. Used around single awaits
    in the agent's async generator, never around a `yield`, so the context is always restored
    in the same task that set it."""
    token = context.attach(trace.set_span_in_context(span))
    try:
        yield
    finally:
        context.detach(token)


def elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def setup_tracing() -> TracerProvider:
    """Install the global tracer provider, printing spans to the console. Without it, spans are
    no-ops (almost free). In production: a BatchSpanProcessor with an OTLP exporter."""
    provider = TracerProvider(resource=Resource.create({"service.name": "platform-copilot"}))
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    return provider
