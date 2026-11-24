"""Feedback loop, judge calibration and the drift report."""

from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from tokens import bearer

from platform_copilot import feedback, monitoring
from platform_copilot.config import Settings
from platform_copilot.db import Pool
from platform_copilot.evaluation import cohen_kappa
from platform_copilot.feedback import FeedbackIn
from platform_copilot.monitoring import length_bucket, level, percentile, psi

Conn = psycopg.AsyncConnection[dict[str, object]]
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


async def log(conn: Conn, days_ago: float, kind: str, payload: dict[str, Any], conv: str) -> None:
    await conn.execute(
        "INSERT INTO audit_log (ts, user_id, conversation_id, kind, payload)"
        " VALUES (%s, 'laura@sales', %s, %s, %s)",
        (NOW - timedelta(days=days_ago), conv, kind, Jsonb(payload)),
    )


# ---------- feedback loop ----------


async def test_thumbs_down_becomes_a_candidate_eval_case(pool: Pool, conn: Conn) -> None:
    await log(conn, 0, "question", {"text": "¿Quién aprueba PII?"}, "c1")
    await log(conn, 0, "answer", {"text": "No lo sé."}, "c1")
    bad = FeedbackIn(conversation_id="c1", rating=-1, comment="Está en la política")
    await feedback.record(pool, "laura@sales", bad)
    await feedback.record(pool, "laura@sales", FeedbackIn(conversation_id="c1", rating=1))
    found = await feedback.candidates(pool, NOW - timedelta(days=1))
    assert found == [{
        "id": "feedback-1", "user": "laura@sales", "question": "¿Quién aprueba PII?",
        "bad_answer": "No lo sé.", "comment": "Está en la política", "status": "needs_label",
    }]  # fmt: skip


async def test_the_export_script_prints_jsonl(
    pool: Pool,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await feedback.record(pool, "laura@sales", FeedbackIn(conversation_id="c9", rating=-1))
    monkeypatch.setattr(feedback, "get_settings", lambda: settings)
    await feedback.main(NOW - timedelta(days=3650))
    assert '"id": "feedback-1"' in capsys.readouterr().out


def test_feedback_api(client: TestClient) -> None:
    body = {"conversation_id": "c1", "rating": -1, "comment": "mal"}
    response = client.post("/api/feedback", headers=bearer("laura@sales"), json=body)
    assert (response.status_code, response.json()) == (201, {"id": 1})
    invalid = {"conversation_id": "c1", "rating": 5}
    assert (
        client.post("/api/feedback", headers=bearer("laura@sales"), json=invalid).status_code == 422
    )


# ---------- judge calibration ----------


@pytest.mark.parametrize(
    ("judge", "human", "kappa"),
    [
        ([True, True, False, False], [True, True, False, False], 1.0),
        # Agrees on 8 of 10 only because almost everything is "faithful": kappa says no better
        # than chance.
        ([True] * 10, [True] * 8 + [False] * 2, 0.0),
        ([True, False, True, False], [True, True, False, False], 0.0),
        ([True, True], [True, True], 1.0),
        ([], [], None),
    ],
)
def test_cohen_kappa(judge: list[bool], human: list[bool], kappa: float | None) -> None:
    assert cohen_kappa(judge, human) == kappa


def test_cohen_kappa_needs_paired_labels() -> None:
    with pytest.raises(ValueError, match="same length"):
        cohen_kappa([True], [])


# ---------- drift ----------


def test_psi_levels() -> None:
    same = psi({"search_docs": 50, "query_catalog": 50}, {"search_docs": 50, "query_catalog": 50})
    shifted = psi(
        {"search_docs": 90, "query_catalog": 10}, {"search_docs": 10, "query_catalog": 90}
    )
    assert (same, level(same)) == (0.0, "stable")
    assert level(shifted) == "alert"
    assert level(0.15) == "watch"
    assert psi({}, {"new_tool": 3}) > 0  # a tool never seen before counts as drift


def test_helpers() -> None:
    assert [length_bucket("x" * n) for n in (10, 80, 500)] == ["short", "medium", "long"]
    assert percentile([], 0.5) is None
    assert percentile([100, 200, 300, 400], 0.95) == 400
    assert percentile([100, 200, 300, 400], 0.5) == 200


async def test_drift_report_compares_two_windows(pool: Pool, conn: Conn) -> None:
    # Baseline week: short docs questions answered by search_docs.
    for i in range(4):
        await log(conn, 10, "question", {"text": "¿guardias?"}, f"b{i}")
        await log(conn, 10, "tool_call", {"tool": "search_docs"}, f"b{i}")
        await log(conn, 10, "answer", {"ms": 1000, "cost_usd": 0.001}, f"b{i}")
    # This week: long catalog questions, slower, one model error.
    for i in range(4):
        await log(conn, 2, "question", {"text": "x" * 200}, f"c{i}")
        await log(conn, 2, "tool_call", {"tool": "query_catalog"}, f"c{i}")
        await log(conn, 2, "answer", {"ms": 4000 + i, "cost_usd": 0.003}, f"c{i}")
    await log(conn, 2, "error", {"code": "llm_error"}, "c0")
    report = await monitoring.drift_report(pool, NOW, days=7)
    assert report["drift"]["tool_mix"]["level"] == "alert"
    assert report["drift"]["question_length"]["level"] == "alert"
    current, baseline = report["current"], report["baseline"]
    assert (current["questions"], current["error_rate"]) == (4, 0.25)
    assert (baseline["latency_p95_ms"], current["latency_p95_ms"]) == (1000.0, 4003.0)
    assert current["cost_per_answer_usd"] == pytest.approx(0.003)
    assert current["thumbs_down_rate"] is None  # nobody rated anything


async def test_an_empty_window_has_no_numbers(pool: Pool) -> None:
    empty = await monitoring.snapshot(pool, monitoring.Window(NOW, NOW))
    assert (empty["questions"], empty["error_rate"], empty["cost_per_answer_usd"]) == (
        0,
        None,
        None,
    )


def test_monitoring_api_is_for_the_platform_team(client: TestClient) -> None:
    assert client.get("/api/monitoring", headers=bearer("laura@sales")).status_code == 403
    report = client.get("/api/monitoring?days=1", headers=bearer("dev@platform")).json()
    assert report["window_days"] == 1
    assert set(report["drift"]) == {"tool_mix", "question_length"}
