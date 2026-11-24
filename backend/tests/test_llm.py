import json
from typing import Any

import httpx
import httpx2
import pytest
from anthropic import DefaultAsyncHttpxClient

from platform_copilot.config import Settings
from platform_copilot.guards import MaskingProvider
from platform_copilot.llm.base import ChatReply, LLMError, ToolCall, assistant_message
from platform_copilot.llm.claude import ClaudeProvider
from platform_copilot.llm.factory import build_llm
from platform_copilot.llm.fake import FakeProvider
from platform_copilot.llm.fallback import FallbackProvider
from platform_copilot.llm.ollama import OllamaProvider
from platform_copilot.llm.vertex import VertexProvider

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_docs",
            "description": "Search",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
    }
]
CALL = ToolCall("call_0", "search_docs", {"query": "pii"})
CONVERSATION: list[dict[str, Any]] = [
    {"role": "system", "content": "Eres el copilot."},
    {"role": "user", "content": "¿PII?"},
    assistant_message(ChatReply("", [CALL], raw=[{"type": "tool_use", "id": "call_0"}])),
    {"role": "tool", "tool_call_id": "call_0", "name": "search_docs", "content": "{}",
     "is_error": False},
    {"role": "tool", "tool_call_id": "call_1", "name": "search_docs", "content": "{}",
     "is_error": True},
]  # fmt: skip


# ---------- Ollama ----------


def ollama(handler: Any) -> OllamaProvider:
    return OllamaProvider("http://ollama/", "qwen", transport=httpx.MockTransport(handler))


async def test_ollama_sends_tools_and_parses_tool_calls() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        message = {
            "content": "",
            "tool_calls": [
                {"function": {"name": "search_docs", "arguments": {"query": "pii"}}},
                {"function": {"name": "query_catalog", "arguments": '{"sql": "SELECT 1"}'}},
                {"function": {"name": "query_catalog", "arguments": "not json"}},
                {"function": {"name": "query_catalog", "arguments": "[1]"}},
            ],
        }
        return httpx.Response(
            200, json={"message": message, "prompt_eval_count": 50, "eval_count": 9}
        )

    reply = await ollama(handler).chat(CONVERSATION, TOOLS)
    assert [c.arguments for c in reply.tool_calls] == [
        {"query": "pii"},
        {"sql": "SELECT 1"},
        {},
        {},
    ]
    assert [c.id for c in reply.tool_calls][:2] == ["call_0", "call_1"]
    assert (reply.input_tokens, reply.output_tokens) == (50, 9)
    assert seen["tools"] == TOOLS
    assert seen["messages"][2] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "search_docs", "arguments": {"query": "pii"}}}],
    }
    assert seen["messages"][3] == {"role": "tool", "content": "{}", "tool_name": "search_docs"}


async def test_ollama_plain_answer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "Hola"}})

    reply = await ollama(handler).chat([{"role": "user", "content": "hola"}], [])
    assert (reply.text, reply.tool_calls, reply.input_tokens) == ("Hola", [], 0)


async def test_ollama_errors() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with pytest.raises(LLMError, match="in time"):
        await ollama(timeout).chat([], [])
    with pytest.raises(LLMError, match="Cannot use Ollama"):
        await ollama(broken).chat([], [])


async def test_ollama_readiness() -> None:
    def ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": []})

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    assert await ollama(ok).is_ready() is True
    assert await ollama(down).is_ready() is False


# ---------- Claude ----------


def claude(handler: Any) -> ClaudeProvider:
    client = DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))
    return ClaudeProvider("claude-opus-5-5", api_key="test-key", http_client=client)


def message(content: list[dict[str, Any]], stop_reason: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 7},
    }


async def test_claude_request_shape_and_tool_use() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta")
        content: list[dict[str, Any]] = [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": "Busco. "},
            {"type": "tool_use", "id": "toolu_1", "name": "search_docs", "input": {"query": "pii"}},
        ]
        return httpx2.Response(200, json=message(content, "tool_use"))

    reply = await claude(handler).chat(CONVERSATION, TOOLS)
    assert reply.text == "Busco. "
    assert reply.tool_calls == [ToolCall("toolu_1", "search_docs", {"query": "pii"})]
    assert (reply.model, reply.input_tokens, reply.output_tokens) == ("claude-opus-5-5", 12, 7)
    assert [block.type for block in reply.raw] == ["thinking", "text", "tool_use"]
    body = seen["body"]
    assert body["system"] == "Eres el copilot."
    assert body["fallbacks"] == "default"
    assert body["output_config"] == {"effort": "medium"}
    assert seen["beta"] == "server-side-fallback-2026-07-01"
    assert body["tools"][0]["input_schema"] == TOOLS[0]["function"]["parameters"]
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    results = body["messages"][2]["content"]
    assert [(r["tool_use_id"], r["is_error"]) for r in results] == [
        ("call_0", False),
        ("call_1", True),
    ]


async def test_claude_refusal_and_errors() -> None:
    def refusal(request: httpx2.Request) -> httpx2.Response:
        no_content: list[dict[str, Any]] = []
        return httpx2.Response(200, json=message(no_content, "refusal"))

    def overloaded(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            400, json={"type": "error", "error": {"type": "x", "message": "bad"}}
        )

    def unreachable(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused")

    with pytest.raises(LLMError, match="declined"):
        await claude(refusal).chat([{"role": "user", "content": "x"}], [])
    with pytest.raises(LLMError, match="Claude API error 400"):
        await claude(overloaded).chat([{"role": "user", "content": "x"}], [])
    provider = ClaudeProvider(
        "claude-opus-5-5",
        api_key="test-key",
        http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(unreachable)),
    )
    provider._client = provider._client.with_options(max_retries=0)
    with pytest.raises(LLMError, match="Cannot reach"):
        await provider.chat([{"role": "user", "content": "x"}], [])
    assert await provider.is_ready() is True


# ---------- factory and fake ----------


def test_factory_follows_settings() -> None:
    assert isinstance(build_llm(Settings(llm_provider="ollama")), OllamaProvider)
    assert isinstance(build_llm(Settings(llm_provider="fake")), FakeProvider)
    claude_llm = build_llm(Settings(llm_provider="claude", anthropic_api_key="k"))
    assert isinstance(claude_llm, MaskingProvider)  # external: PII masked on the way out
    assert isinstance(claude_llm.inner, ClaudeProvider)
    assert claude_llm.model == "claude-opus-5-5"
    unmasked = build_llm(Settings(llm_provider="claude", anthropic_api_key="k",
                                  mask_pii_external=False))  # fmt: skip
    assert isinstance(unmasked, ClaudeProvider)
    vertex = build_llm(Settings(llm_provider="vertex", gcp_project="p", llm_fallback="ollama"))
    assert isinstance(vertex, FallbackProvider)
    assert isinstance(vertex.primary, MaskingProvider)
    assert isinstance(vertex.primary.inner, VertexProvider)
    assert isinstance(vertex.fallback, OllamaProvider)
    local = build_llm(Settings(llm_provider="ollama", llm_fallback="ollama"))
    assert isinstance(local, OllamaProvider)  # no point falling back to itself


async def test_fake_provider_runs_out_loudly() -> None:
    fake = FakeProvider()
    assert await fake.is_ready() is True
    with pytest.raises(AssertionError, match="ran out"):
        await fake.chat([], [])
