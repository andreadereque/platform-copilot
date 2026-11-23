"""Hybrid search: vector similarity + Spanish full-text, fused with Reciprocal Rank Fusion.

The group filter is inside both queries: a chunk the user may not see never reaches the model.
"""

from dataclasses import dataclass

from platform_copilot.db import Pool
from platform_copilot.embeddings import to_pgvector

RRF_K = 60
CANDIDATES = 20

HYBRID_SQL = """
WITH v AS (
    SELECT id, row_number() OVER (ORDER BY embedding <=> %(emb)s::vector) AS r
    FROM chunks WHERE "group" = ANY(%(groups)s)
    ORDER BY embedding <=> %(emb)s::vector LIMIT %(candidates)s
),
t AS (
    SELECT id, row_number() OVER (ORDER BY ts_rank(tsv, q) DESC) AS r
    FROM chunks, websearch_to_tsquery('spanish', %(query)s) AS q
    WHERE tsv @@ q AND "group" = ANY(%(groups)s)
    ORDER BY ts_rank(tsv, q) DESC LIMIT %(candidates)s
),
fused AS (
    SELECT id, SUM(1.0 / (%(k)s + r)) AS score
    FROM (SELECT * FROM v UNION ALL SELECT * FROM t) AS both_lists
    GROUP BY id
)
SELECT c.source_id, c.heading, c.content, f.score::float AS score
FROM fused f JOIN chunks c USING (id)
ORDER BY f.score DESC, c.source_id
LIMIT %(top_k)s
"""


@dataclass(frozen=True)
class Hit:
    source_id: str
    heading: str
    content: str
    score: float


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
        "k": RRF_K,
        "top_k": top_k,
    }
    async with pool.connection() as conn:
        rows = await (await conn.execute(HYBRID_SQL, params)).fetchall()
    return [Hit(**row) for row in rows]
