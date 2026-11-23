from collections.abc import AsyncIterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from platform_copilot import audit
from platform_copilot.access import AccessError, create_request, decide, get_request
from platform_copilot.config import Settings
from platform_copilot.db import Pool, make_pool
from platform_copilot.embeddings import FakeEmbedder
from platform_copilot.tools.base import ToolContext, ToolError
from platform_copilot.tools.create_access_request import TOOL, CreateAccessRequestArgs
from platform_copilot.users import User

Conn = psycopg.AsyncConnection[dict[str, object]]
LAURA = User("laura@sales", "Laura Gil", ["sales", "public"])


@pytest.fixture
async def pool(conn: Conn, settings: Settings) -> AsyncIterator[Pool]:
    db_pool = make_pool(settings.database_url)
    await db_pool.open()
    yield db_pool
    await db_pool.close()


async def test_create_request_is_pending_with_the_owner(pool: Pool) -> None:
    request = await create_request(pool, "sales_es_monthly", "laura@sales", "informe trimestral")
    assert (request.id, request.status, request.owner, request.pii) == (
        1,
        "pending",
        "marta@finance",
        True,
    )


@pytest.mark.parametrize(
    ("product", "requester", "message"),
    [
        ("nope", "laura@sales", "does not exist"),
        ("sales_es_weekly", "laura@sales", "already owns"),
    ],
)
async def test_create_request_rejects(
    pool: Pool, product: str, requester: str, message: str
) -> None:
    with pytest.raises(AccessError, match=message):
        await create_request(pool, product, requester, "motivo")


async def test_only_one_pending_request_per_product_and_user(pool: Pool) -> None:
    await create_request(pool, "sales_es_monthly", "laura@sales", "informe")
    with pytest.raises(AccessError, match="already a pending request #1"):
        await create_request(pool, "sales_es_monthly", "laura@sales", "otra vez")


async def test_owner_decides_and_it_is_audited(pool: Pool) -> None:
    await create_request(pool, "sales_es_monthly", "laura@sales", "informe")
    decided = await decide(pool, 1, "marta@finance", "approved")
    assert (decided.status, decided.decided_by) == ("approved", "marta@finance")
    assert decided.decided_at is not None
    events = await audit.recent(pool, 5)
    assert events[0]["kind"] == "decision"
    assert events[0]["payload"] == {"request_id": 1, "status": "approved"}


@pytest.mark.parametrize(
    ("decider", "code", "message"),
    [
        ("laura@sales", 403, "own request"),
        ("dev@platform", 403, "Only the owner"),
    ],
)
async def test_only_the_owner_can_decide(pool: Pool, decider: str, code: int, message: str) -> None:
    await create_request(pool, "sales_es_monthly", "laura@sales", "informe")
    with pytest.raises(AccessError, match=message) as error:
        await decide(pool, 1, decider, "approved")
    assert error.value.status_code == code


async def test_a_decision_is_final(pool: Pool) -> None:
    await create_request(pool, "sales_es_monthly", "laura@sales", "informe")
    await decide(pool, 1, "marta@finance", "rejected")
    with pytest.raises(AccessError, match="already rejected"):
        await decide(pool, 1, "marta@finance", "approved")


async def test_unknown_request(pool: Pool) -> None:
    with pytest.raises(AccessError, match="#99 does not exist"):
        await get_request(pool, 99)


async def test_tool_creates_a_pending_request(pool: Pool, settings: Settings) -> None:
    ctx = ToolContext(pool, LAURA, FakeEmbedder(), settings, "c1")
    args = CreateAccessRequestArgs(product="sales_es_monthly", reason="informe trimestral")
    output = await TOOL.run(args, ctx)
    assert output.content["status"] == "pending"
    assert output.content["owner"] == "marta@finance"
    assert output.summary == "#1"
    with pytest.raises(ToolError, match="already a pending request"):
        await TOOL.run(args, ctx)


# ---------- API ----------

MARTA = {"X-User-Id": "marta@finance"}


def test_users_endpoint(client: TestClient) -> None:
    ids = [user["id"] for user in client.get("/api/users").json()]
    assert "laura@sales" in ids


def test_unknown_user_is_401(client: TestClient) -> None:
    response = client.get("/api/access-requests", headers={"X-User-Id": "intruder"})
    assert response.status_code == 401


async def test_owner_sees_and_decides_requests(client: TestClient, pool: Pool) -> None:
    await create_request(pool, "sales_es_monthly", "laura@sales", "informe")
    await create_request(pool, "finance_invoices_es", "laura@sales", "auditoría")
    listed = client.get("/api/access-requests", headers=MARTA).json()
    assert [item["id"] for item in listed] == [2, 1]
    assert client.get("/api/access-requests", headers={"X-User-Id": "laura@sales"}).json() == []
    approved = client.post("/api/access-requests/1/approve", headers=MARTA)
    assert approved.json()["status"] == "approved"
    rejected = client.post("/api/access-requests/2/reject", headers=MARTA)
    assert rejected.json()["status"] == "rejected"
    again = client.post("/api/access-requests/2/approve", headers=MARTA)
    assert again.status_code == 409


async def test_audit_is_only_for_the_platform_team(client: TestClient, pool: Pool) -> None:
    await audit.record(pool, "laura@sales", "c1", "question", {"text": "hola"})
    assert client.get("/api/audit", headers=MARTA).status_code == 403
    events = client.get("/api/audit?limit=5", headers={"X-User-Id": "dev@platform"}).json()
    assert events[0]["payload"] == {"text": "hola"}
