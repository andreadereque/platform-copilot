"""Production feedback loop: users rate answers; bad ones become candidate eval cases.

    👎 in the UI → feedback row → `candidates()` joins it with the question and answer from the
    audit log → a person reviews and labels it → it joins evals/cases.json → CI runs it forever.

So every failure a user reports can become a regression test, and the eval set grows from real
questions instead of only the ones I imagined.

Usage: `uv run python -m platform_copilot.feedback > ../evals/candidates.jsonl`
"""

import asyncio
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from platform_copilot.config import get_settings
from platform_copilot.db import Pool, make_pool


class FeedbackIn(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=64)
    rating: Literal[-1, 1]
    comment: str = Field(default="", max_length=1000)


async def record(pool: Pool, user_id: str, feedback: FeedbackIn) -> int:
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "INSERT INTO feedback (conversation_id, user_id, rating, comment)"
                " VALUES (%s, %s, %s, %s) RETURNING id",
                (feedback.conversation_id, user_id, feedback.rating, feedback.comment),
            )
        ).fetchone()
    assert row is not None
    return int(row["id"])


CANDIDATES_SQL = """
SELECT f.id, f.conversation_id, f.user_id, f.comment, f.created_at,
       (SELECT payload->>'text' FROM audit_log a
         WHERE a.conversation_id = f.conversation_id AND a.kind = 'question'
           AND a.ts <= f.created_at ORDER BY a.ts DESC LIMIT 1) AS question,
       (SELECT payload->>'text' FROM audit_log a
         WHERE a.conversation_id = f.conversation_id AND a.kind = 'answer'
           AND a.ts <= f.created_at ORDER BY a.ts DESC LIMIT 1) AS answer
FROM feedback f
WHERE f.rating = -1 AND f.created_at >= %(since)s
ORDER BY f.created_at
"""


async def candidates(pool: Pool, since: datetime) -> list[dict[str, Any]]:
    """Answers users disliked, with the question that produced them, ready for human review."""
    async with pool.connection() as conn:
        rows = await (await conn.execute(CANDIDATES_SQL, {"since": since})).fetchall()
    return [
        {
            "id": f"feedback-{row['id']}",
            "user": row["user_id"],
            "question": row["question"],
            "bad_answer": row["answer"],
            "comment": row["comment"],
            "status": "needs_label",  # a person adds the expected tool/source/answer
        }
        for row in rows
    ]


async def main(since: datetime) -> None:
    pool = make_pool(get_settings().database_url)
    await pool.open()
    try:
        for case in await candidates(pool, since):
            print(json.dumps(case, ensure_ascii=False))
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main(datetime.fromisoformat("2000-01-01T00:00:00+00:00")))
