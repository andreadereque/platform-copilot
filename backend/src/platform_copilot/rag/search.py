"""Hybrid search: vector similarity + Spanish full-text, fused with Reciprocal Rank Fusion.

The group filter is inside both queries: a chunk the user may not see never reaches the model.

The two queries run *concurrently*, each on its own pool connection (one connection runs one
query at a time). Concurrency, not parallelism: there is one Python thread and one event loop.
While the vector query waits for PostgreSQL, `await` hands the loop to the full-text query, so
both wait at the same time and the search takes max(a, b) instead of a + b. The real work
happens in parallel in two PostgreSQL backend processes, not in Python. That is what asyncio is
for, I/O-bound work; CPU-bound work in Python (e.g. embedding locally) would need processes.
"""

import asyncio
from dataclasses import dataclass
from typing import Any

from platform_copilot.db import Pool
from platform_copilot.embeddings import to_pgvector

RRF_K = 60
CANDIDATES = 20

VECTOR_SQL = """
SELECT source_id, heading, content
FROM chunks WHERE "group" = ANY(%(groups)s)
ORDER BY embedding <=> %(emb)s::vector LIMIT %(candidates)s
"""

TEXT_SQL = """
SELECT source_id, heading, content
FROM chunks, websearch_to_tsquery('spanish', %(query)s) AS q
WHERE tsv @@ q AND "group" = ANY(%(groups)s)
ORDER BY ts_rank(tsv, q) DESC, source_id LIMIT %(candidates)s
"""


@dataclass(frozen=True)
class Hit:
    source_id: str
    heading: str
    content: str
    score: float


Ranked = list[dict[str, Any]]


async def fetch_ranked(pool: Pool, sql: str, params: dict[str, Any]) -> Ranked:
    """One ranked candidate list. `pool.connection()` lends this coroutine its own connection."""
    async with pool.connection() as conn:
        return list(await (await conn.execute(sql, params)).fetchall())


def rrf_fuse(rankings: list[Ranked], top_k: int) -> list[Hit]:
    """Reciprocal Rank Fusion: score = sum of 1 / (k + rank) over the lists a chunk appears in.

    Ranks, not raw scores: cosine distance and ts_rank are not comparable, positions are."""
    scores: dict[str, float] = {}
    rows: dict[str, dict[str, Any]] = {}
    for ranking in rankings:
        for rank, row in enumerate(ranking, start=1):
            scores[row["source_id"]] = scores.get(row["source_id"], 0.0) + 1.0 / (RRF_K + rank)
            rows[row["source_id"]] = row
    best = sorted(scores, key=lambda source_id: (-scores[source_id], source_id))[:top_k]
    return [Hit(**rows[source_id], score=scores[source_id]) for source_id in best]


async def hybrid_search(
    pool: Pool,
    query: str,
    query_embedding: list[float],
    groups: list[str],
    top_k: int,
) -> list[Hit]:
    params = {
        "emb": to_pgvector(query_embedding),
        "groups": groups,
        "query": query,
        "candidates": CANDIDATES,
    }
    # Both queries are sent before either answer arrives (see the module docstring).
    by_vector, by_text = await asyncio.gather(
        fetch_ranked(pool, VECTOR_SQL, params),
        fetch_ranked(pool, TEXT_SQL, params),
    )
    return rrf_fuse([by_vector, by_text], top_k)
