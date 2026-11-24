"""Production monitoring and drift: compare the last window with the one before it.

Everything comes from the audit log (questions, tool calls, answers with latency, tokens and
cost) and the feedback table, so no extra pipeline is needed.

Two kinds of signal:

- **Health:** questions, error rate, p50/p95 latency, tokens and cost per answer, share of
  thumbs-down. A jump in p95 latency or cost is often the first sign of a provider problem.
- **Drift:** has what users ask, or what the agent does, changed? With an LLM you do not
  retrain, but the world moves: new products, new kinds of questions, a provider updates the
  model behind the same name. Measured with the Population Stability Index (PSI) over:
    * the tool mix (which tools the agent chose): a change in *agent behaviour*;
    * question length buckets: a cheap proxy for a change in *input distribution*.
  Rule of thumb: PSI < 0.1 stable, 0.1-0.2 watch, > 0.2 investigate.

In production the same queries would feed a dashboard and alerts (Cloud Monitoring, Grafana).
Embedding-based drift (distance between this week's question embeddings and last month's) is
the next step; the embeddings are already computed for retrieval.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from platform_copilot.db import Pool

EPSILON = 1e-4  # PSI is undefined for empty buckets; a tiny share keeps it finite
WATCH, ALERT = 0.1, 0.2
LENGTH_BUCKETS = [(40, "short"), (120, "medium")]  # characters; longer is "long"


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime


def psi(expected: dict[str, int], actual: dict[str, int]) -> float:
    """Population Stability Index: sum of (a - e) * ln(a / e) over the buckets' shares."""
    keys = set(expected) | set(actual)
    total_e = sum(expected.values()) or 1
    total_a = sum(actual.values()) or 1
    score = 0.0
    for key in keys:
        e = max(expected.get(key, 0) / total_e, EPSILON)
        a = max(actual.get(key, 0) / total_a, EPSILON)
        score += (a - e) * math.log(a / e)
    return round(score, 4)


def level(score: float) -> str:
    if score > ALERT:
        return "alert"
    if score > WATCH:
        return "watch"
    return "stable"


def length_bucket(text: str) -> str:
    for limit, name in LENGTH_BUCKETS:
        if len(text) < limit:
            return name
    return "long"


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile; None without data."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(math.ceil(q * len(ordered)) - 1, 0)
    return ordered[rank]


async def _rows(pool: Pool, window: Window) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT kind, payload FROM audit_log WHERE ts >= %s AND ts < %s",
            (window.start, window.end),
        )
        return list(await cursor.fetchall())


async def _thumbs_down(pool: Pool, window: Window) -> tuple[int, int]:
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT count(*) FILTER (WHERE rating = -1) AS down, count(*) AS total"
                " FROM feedback WHERE created_at >= %s AND created_at < %s",
                (window.start, window.end),
            )
        ).fetchone()
    assert row is not None
    return int(row["down"]), int(row["total"])


async def snapshot(pool: Pool, window: Window) -> dict[str, Any]:
    rows = await _rows(pool, window)
    questions = [r["payload"]["text"] for r in rows if r["kind"] == "question"]
    answers = [r["payload"] for r in rows if r["kind"] == "answer"]
    errors = [r for r in rows if r["kind"] == "error"]
    tools: dict[str, int] = {}
    for r in rows:
        if r["kind"] == "tool_call":
            tools[r["payload"]["tool"]] = tools.get(r["payload"]["tool"], 0) + 1
    lengths: dict[str, int] = {}
    for question in questions:
        lengths[length_bucket(question)] = lengths.get(length_bucket(question), 0) + 1
    latencies = [float(a["ms"]) for a in answers if "ms" in a]
    costs = [float(a["cost_usd"]) for a in answers if "cost_usd" in a]
    down, rated = await _thumbs_down(pool, window)
    return {
        "questions": len(questions),
        "error_rate": _share(len(errors), len(questions)),
        "latency_p50_ms": percentile(latencies, 0.5),
        "latency_p95_ms": percentile(latencies, 0.95),
        "cost_per_answer_usd": _mean(costs),
        "thumbs_down_rate": _share(down, rated),
        "tool_mix": tools,
        "question_length": lengths,
    }


async def drift_report(pool: Pool, now: datetime, days: int = 7) -> dict[str, Any]:
    """The last `days` against the `days` before them."""
    current = Window(now - timedelta(days=days), now)
    baseline = Window(now - timedelta(days=2 * days), current.start)
    before, after = await snapshot(pool, baseline), await snapshot(pool, current)
    tool_psi = psi(before["tool_mix"], after["tool_mix"])
    length_psi = psi(before["question_length"], after["question_length"])
    return {
        "window_days": days,
        "baseline": before,
        "current": after,
        "drift": {
            "tool_mix": {"psi": tool_psi, "level": level(tool_psi)},
            "question_length": {"psi": length_psi, "level": level(length_psi)},
        },
    }


def _share(part: int, whole: int) -> float | None:
    if whole == 0:
        return None
    return round(part / whole, 4)


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)
