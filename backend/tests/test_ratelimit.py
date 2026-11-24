from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from tokens import bearer, make_validator

from platform_copilot.config import Settings
from platform_copilot.db import Pool
from platform_copilot.embeddings import FakeEmbedder
from platform_copilot.llm.base import ChatReply
from platform_copilot.llm.fake import FakeProvider
from platform_copilot.main import create_app
from platform_copilot.ratelimit import RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_burst_then_one_per_refill() -> None:
    clock = FakeClock()
    limiter = RateLimiter(capacity=3, refill_per_s=0.5, clock=clock)
    assert [limiter.acquire("laura") for _ in range(3)] == [0, 0, 0]
    assert limiter.acquire("laura") == pytest.approx(2.0)  # one token takes 2 s to come back
    clock.now += 1
    assert limiter.acquire("laura") == pytest.approx(1.0)  # half a token so far
    clock.now += 1
    assert limiter.acquire("laura") == 0


def test_users_have_their_own_bucket() -> None:
    limiter = RateLimiter(capacity=1, refill_per_s=1, clock=FakeClock())
    assert limiter.acquire("laura") == 0
    assert limiter.acquire("laura") > 0
    assert limiter.acquire("marta") == 0


def test_the_bucket_never_holds_more_than_its_capacity() -> None:
    clock = FakeClock()
    limiter = RateLimiter(capacity=2, refill_per_s=1, clock=clock)
    limiter.acquire("laura")
    clock.now += 3600  # an hour idle does not buy an hour of questions
    assert [limiter.acquire("laura") > 0 for _ in range(3)] == [False, False, True]


@pytest.fixture
def limited_client(settings: Settings, pool: Pool) -> Iterator[TestClient]:
    llm = FakeProvider()
    llm.replies = [ChatReply("Hola")] * 5
    limited = settings.model_copy(update={"chat_burst": 2, "chat_per_minute": 1})
    app = create_app(limited, llm=llm, embedder=FakeEmbedder(), token_validator=make_validator())
    with TestClient(app) as client:
        yield client


def test_chat_returns_429_with_retry_after(limited_client: TestClient) -> None:
    laura, marta = bearer("laura@sales"), bearer("marta@finance")
    codes = [
        limited_client.post("/api/chat", headers=laura, json={"message": "hola"}).status_code
        for _ in range(3)
    ]
    assert codes == [200, 200, 429]
    blocked = limited_client.post("/api/chat", headers=laura, json={"message": "hola"})
    assert blocked.json() == {"detail": "Too many questions, wait a moment"}
    assert blocked.headers["retry-after"] == "60"
    assert limited_client.post("/api/chat", headers=marta, json={"message": "hola"}).is_success
