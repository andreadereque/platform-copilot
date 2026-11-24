"""The agent loop: ask the model, run the tools it picks, repeat until it answers (max N steps).

`run_agent` is an async generator of events, so the API can stream every step to the browser
(SSE) and the tests can collect them in a list.
"""

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from opentelemetry.trace import Span, Status, StatusCode
from pydantic import ValidationError

from platform_copilot import audit, prompts
from platform_copilot.context import clip, fit_history
from platform_copilot.llm.base import (
    ChatReply,
    LLMError,
    LLMProvider,
    Message,
    ToolCall,
    assistant_message,
)
from platform_copilot.observability import child_of, elapsed_ms, tracer
from platform_copilot.tools import create_access_request, query_catalog, search_docs
from platform_copilot.tools.base import Tool, ToolContext, ToolError
from platform_copilot.users import User

DEFAULT_PROMPT_VERSION = "v1"

DEFAULT_TOOLS = {
    tool.name: tool for tool in (search_docs.TOOL, query_catalog.TOOL, create_access_request.TOOL)
}

CITATION = re.compile(r"\[([^\[\]]+)\]")
WORDS_PER_TOKEN_EVENT = 3


@dataclass(frozen=True)
class Event:
    name: str
    data: dict[str, Any]


@dataclass(frozen=True)
class ToolOutcome:
    ok: bool
    content: dict[str, Any]
    summary: str
    sources: list[str] = field(default_factory=list)


def system_prompt(user: User, version: str = DEFAULT_PROMPT_VERSION) -> str:
    """The versioned system prompt (prompts/system.<version>.md) filled in for this user."""
    prompt = prompts.load("system", version)
    return prompt.render(user_id=user.id, user_name=user.name, groups=", ".join(user.groups))


async def run_agent(
    question: str,
    history: list[Message],
    ctx: ToolContext,
    llm: LLMProvider,
    tools: dict[str, Tool] | None = None,
) -> AsyncIterator[Event]:
    """Run the agent and trace it: one `agent.run` span, parent of every model and tool span."""
    if tools is None:
        tools = DEFAULT_TOOLS
    span = tracer.start_span("agent.run")
    span.set_attribute("user.id", ctx.user.id)
    span.set_attribute("conversation.id", ctx.conversation_id)
    span.set_attribute("gen_ai.request.model", llm.model)
    span.set_attribute("prompt.version", ctx.settings.system_prompt_version)
    try:
        async for event in _loop(question, history, ctx, llm, tools, span):
            _record_outcome(span, event)
            yield event
    finally:
        span.end()


def _record_outcome(span: Span, event: Event) -> None:
    if event.name == "error":
        span.set_attribute("outcome", event.data["code"])
        span.set_status(Status(StatusCode.ERROR, event.data["message"]))
    if event.name == "done":
        span.set_attribute("outcome", "answered")
        for key in ("steps", "tokens", "ms", "cost_usd"):
            span.set_attribute(key, event.data[key])


async def _loop(
    question: str,
    history: list[Message],
    ctx: ToolContext,
    llm: LLMProvider,
    tools: dict[str, Tool],
    span: Span,
) -> AsyncIterator[Event]:
    max_steps = ctx.settings.max_steps
    version = ctx.settings.system_prompt_version
    messages: list[Message] = [
        {"role": "system", "content": system_prompt(ctx.user, version)},
        *fit_history(history, ctx.settings.history_token_budget),  # oldest turns go first
        {"role": "user", "content": question},
    ]
    schemas = [tool.schema() for tool in tools.values()]
    retrieved: list[str] = []
    tokens = 0
    cost = 0.0
    started = time.perf_counter()
    await _audit(ctx, "question", {"text": question, "prompt_version": f"system.{version}"})

    for step in range(1, max_steps + 1):
        yield Event("step", {"n": step, "max": max_steps})
        try:
            with child_of(span):
                reply = await llm.chat(messages, schemas)
        except LLMError as error:
            await _audit(ctx, "error", {"code": "llm_error", "message": str(error)})
            yield Event("error", {"code": "llm_error", "message": str(error)})
            return
        tokens += reply.input_tokens + reply.output_tokens
        cost += reply.cost_usd
        if not reply.tool_calls and _needs_fallback(step, ctx, tools):
            # Measured with qwen2.5:3b: it often answers "I don't know" without searching. If the
            # first reply used no tool, search the docs with the question and let it try again.
            fallback = ToolCall("fallback-search", search_docs.TOOL.name, {"query": question})
            messages.append(
                assistant_message(ChatReply("", [fallback], raw=_raw_tool_use(fallback)))
            )
            async for event in _execute(fallback, ctx, tools, retrieved, messages, span):
                yield event
            continue
        if not reply.tool_calls:
            stats = {"steps": step, "tokens": tokens, "model": llm.model, "started": started,
                     "cost_usd": cost}  # fmt: skip
            async for event in _finish(reply.text, retrieved, ctx, stats):
                yield event
            return
        messages.append(assistant_message(reply))
        for call in reply.tool_calls:
            async for event in _execute(call, ctx, tools, retrieved, messages, span):
                yield event

    message = f"Stopped after {max_steps} steps without a final answer"
    await _audit(ctx, "error", {"code": "max_steps", "message": message})
    yield Event("error", {"code": "max_steps", "message": message})


def _needs_fallback(step: int, ctx: ToolContext, tools: dict[str, Tool]) -> bool:
    return step == 1 and ctx.settings.search_fallback and search_docs.TOOL.name in tools


def _raw_tool_use(call: ToolCall) -> list[dict[str, Any]]:
    """The same call in Claude's content-block format, so both providers can replay it."""
    return [{"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}]


async def _execute(
    call: ToolCall,
    ctx: ToolContext,
    tools: dict[str, Tool],
    retrieved: list[str],
    messages: list[Message],
    span: Span,
) -> AsyncIterator[Event]:
    """Run one tool call: stream its events, audit it and append its result for the model."""
    yield Event("tool_call", {"id": call.id, "tool": call.name, "args": call.arguments})
    with child_of(span):
        outcome = await run_tool(call, ctx, tools)
    await _audit(
        ctx,
        "tool_call",
        {"tool": call.name, "args": call.arguments, "ok": outcome.ok, "result": outcome.content},
    )
    for source in outcome.sources:
        if source not in retrieved:
            retrieved.append(source)
    yield Event(
        "tool_result",
        {"id": call.id, "tool": call.name, "ok": outcome.ok, "summary": outcome.summary},
    )
    if outcome.ok and call.name == create_access_request.TOOL.name:
        yield Event("approval_pending", {"request_id": outcome.content["request_id"]})
    messages.append(
        {
            "role": "tool",
            "tool_call_id": call.id,
            "name": call.name,
            "content": clip(
                json.dumps(outcome.content, ensure_ascii=False, default=str),
                ctx.settings.tool_result_max_chars,
            ),
            "is_error": not outcome.ok,
        }
    )


async def run_tool(call: ToolCall, ctx: ToolContext, tools: dict[str, Tool]) -> ToolOutcome:
    """Run one tool call, traced as `tool <name>` with its latency and outcome."""
    with tracer.start_as_current_span(f"tool {call.name}") as span:
        started = time.perf_counter()
        outcome = await _run_tool(call, ctx, tools)
        span.set_attribute("gen_ai.tool.name", call.name)
        span.set_attribute("ok", outcome.ok)
        span.set_attribute("latency_ms", elapsed_ms(started))
        if not outcome.ok:
            span.set_status(Status(StatusCode.ERROR, outcome.content["error"]))
        return outcome


async def _run_tool(call: ToolCall, ctx: ToolContext, tools: dict[str, Tool]) -> ToolOutcome:
    """Every failure becomes an error result the model can read and fix."""
    tool = tools.get(call.name)
    if tool is None:
        return _failure(f"Unknown tool {call.name}. Available: {', '.join(tools)}")
    try:
        args = tool.args_model.model_validate(call.arguments)
    except ValidationError as error:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors())
        return _failure(f"Invalid arguments: {problems}")
    try:
        output = await asyncio.wait_for(tool.run(args, ctx), timeout=ctx.settings.tool_timeout_s)
    except TimeoutError:
        return _failure(f"{call.name} took longer than {ctx.settings.tool_timeout_s}s")
    except ToolError as error:
        return _failure(str(error))
    return ToolOutcome(True, output.content, output.summary, output.sources)


def _failure(message: str) -> ToolOutcome:
    return ToolOutcome(False, {"error": message}, "error")


async def _finish(
    text: str, retrieved: list[str], ctx: ToolContext, stats: dict[str, Any]
) -> AsyncIterator[Event]:
    answer, citations, dropped = resolve_citations(text, retrieved)
    if not answer.strip():
        answer = "No he podido generar una respuesta. Prueba a reformular la pregunta."
    words = re.findall(r"\S+\s*", answer)
    for start in range(0, len(words), WORDS_PER_TOKEN_EVENT):
        yield Event("token", {"text": "".join(words[start : start + WORDS_PER_TOKEN_EVENT])})
    for citation in citations:
        yield Event("citation", citation)
    done = {
        "steps": stats["steps"],
        "tokens": stats["tokens"],
        "ms": elapsed_ms(stats["started"]),
        "model": stats["model"],
        "cost_usd": stats["cost_usd"],
    }
    # Latency, tokens and cost go to the audit log too: they are what monitoring and the
    # drift report query, per day and per model.
    await _audit(
        ctx, "answer", {"text": answer, "citations": citations, "dropped": dropped, **done}
    )
    yield Event("done", done)


def resolve_citations(
    text: str, retrieved: list[str]
) -> tuple[str, list[dict[str, Any]], list[str]]:
    """Turn [source-id] markers into [1], [2]... Drop ids the tools never returned."""
    numbers: dict[str, int] = {}
    dropped: list[str] = []

    def replace(match: re.Match[str]) -> str:
        parts = [part.strip() for part in re.split(r"[,;]", match.group(1))]
        if not all(_looks_like_source(part) for part in parts):
            return match.group(0)
        kept = []
        for part in parts:
            if part in retrieved:
                number = numbers.setdefault(part, len(numbers) + 1)
                kept.append(f"[{number}]")
            else:
                dropped.append(part)
        return "".join(kept)

    clean = CITATION.sub(replace, text)
    clean = re.sub(r"[ \t]+([.,;:])", r"\1", clean)
    citations = [{"n": number, "source": source} for source, number in numbers.items()]
    return clean, citations, dropped


def _looks_like_source(part: str) -> bool:
    return "#" in part or part.startswith("catalog:")


async def _audit(ctx: ToolContext, kind: str, payload: dict[str, Any]) -> None:
    await audit.record(ctx.pool, ctx.user.id, ctx.conversation_id, kind, payload)
