"""Prompts as versioned, locked files; context-window budget for history and tool results."""

import json
from pathlib import Path
from typing import Any

import pytest

from platform_copilot import audit, prompts
from platform_copilot.agent import run_agent, system_prompt
from platform_copilot.config import Settings
from platform_copilot.context import clip, estimate_tokens, fit_history
from platform_copilot.db import Pool
from platform_copilot.embeddings import FakeEmbedder
from platform_copilot.llm.base import ChatReply, ToolCall
from platform_copilot.llm.fake import FakeProvider
from platform_copilot.tools.base import ToolContext
from platform_copilot.users import User

LAURA = User("laura@sales", "Laura Gil", ["sales", "public"])


def test_every_prompt_version_matches_its_lock() -> None:
    """Editing a published prompt fails here: add system.v2.md and run `python -m
    platform_copilot.prompts` instead."""
    locked = prompts.lock()
    assert sorted(locked) == prompts.available()
    for prompt_id, sha in locked.items():
        name, version = prompt_id.split(".", 1)
        assert prompts.load(name, version).sha256 == sha, f"{prompt_id} changed after release"


def test_a_prompt_renders_for_the_user() -> None:
    prompt = prompts.load("system", "v1")
    assert prompt.id == "system.v1"
    assert "Current user: laura@sales (Laura Gil), groups: sales, public." in system_prompt(LAURA)


def test_unknown_versions_fail_loudly() -> None:
    with pytest.raises(prompts.UnknownPromptError, match=r"system\.v9"):
        prompts.load("system", "v9")


def test_write_lock_pins_new_versions_and_keeps_old_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "system.v1.md").write_text("one\n")
    (tmp_path / "system.v2.md").write_text("two\n")
    lock_file = tmp_path / "prompts.lock.json"
    lock_file.write_text(json.dumps({"system.v1": "pinned-earlier"}))
    monkeypatch.setattr(prompts, "PROMPTS_DIR", tmp_path)
    monkeypatch.setattr(prompts, "LOCK_FILE", lock_file)
    prompts.load.cache_clear()
    prompts.write_lock()
    prompts.load.cache_clear()
    assert json.loads(lock_file.read_text()) == {
        "system.v1": "pinned-earlier",
        "system.v2": prompts.digest("two"),
    }


def turn(role: str, chars: int) -> dict[str, Any]:
    return {"role": role, "content": "x" * chars}


def test_history_keeps_the_most_recent_turns_that_fit() -> None:
    history = [turn("user", 400), turn("assistant", 400), turn("user", 40), turn("assistant", 40)]
    assert estimate_tokens("x" * 400) == 101
    kept = fit_history(history, budget_tokens=130)
    assert kept == history[1:]  # 101 + 11 + 11 fits; the oldest turn does not
    assert fit_history(history, budget_tokens=5) == []


def test_long_tool_results_are_clipped_visibly() -> None:
    assert clip("short", 10) == "short"
    assert clip("abcdefghij", 4) == "abcd… [truncated 6 characters]"


async def test_the_agent_applies_the_budget_and_records_the_prompt_version(
    pool: Pool, settings: Settings
) -> None:
    llm = FakeProvider([
        ChatReply("", [ToolCall("c1", "search_docs", {"query": "guardias"})]),
        ChatReply("Listo."),
    ])  # fmt: skip
    tight = settings.model_copy(update={"history_token_budget": 20, "tool_result_max_chars": 50})
    ctx = ToolContext(pool, LAURA, FakeEmbedder(), tight, "conv-ctx")
    history = [turn("user", 400), turn("assistant", 40)]
    _ = [event async for event in run_agent("¿guardias?", history, ctx, llm)]
    first_call = llm.calls[0]
    assert [m["role"] for m in first_call] == ["system", "assistant", "user"]  # old turn dropped
    tool_message = llm.calls[1][-1]
    assert tool_message["content"].endswith("characters]")
    question = (await audit.page(pool, 10)).items[-1]
    assert question["payload"]["prompt_version"] == "system.v1"
