"""Per-user rate limit for /api/chat: a token bucket in memory.

Each user has a bucket of `capacity` tokens. A question takes one; tokens come back at
`refill_per_s`. So a user can ask a burst of `capacity` questions at once, then about one every
1 / refill_per_s seconds. An empty bucket means HTTP 429 with Retry-After.

No lock is needed: `acquire` has no `await`, and asyncio only switches tasks at an `await`, so
two requests on the event loop can never interleave inside it.

Limits: the buckets live in this process. With N API replicas each one counts on its own
(a user gets N times the limit) and a restart forgets them. For that, the same algorithm goes
in a shared store: Redis (INCR + EXPIRE, or a Lua script) or a row per user in PostgreSQL.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class Bucket:
    tokens: float
    updated_at: float


class RateLimiter:
    def __init__(
        self,
        capacity: int,
        refill_per_s: float,
        clock: Callable[[], float] = time.monotonic,  # tests pass a fake clock
    ) -> None:
        self.capacity = capacity
        self.refill_per_s = refill_per_s
        self._clock = clock
        self._buckets: dict[str, Bucket] = {}

    def acquire(self, key: str) -> float:
        """Take one token for `key`. Returns 0 when allowed, or the seconds to wait."""
        now = self._clock()
        bucket = self._buckets.setdefault(key, Bucket(self.capacity, now))
        elapsed = now - bucket.updated_at
        bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self.refill_per_s)
        bucket.updated_at = now
        if bucket.tokens >= 1:
            bucket.tokens -= 1
            return 0.0
        return (1 - bucket.tokens) / self.refill_per_s
