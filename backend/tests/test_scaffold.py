import psycopg
import pytest
from fastapi.testclient import TestClient

from platform_copilot.config import Settings
from platform_copilot.main import create_app

Conn = psycopg.AsyncConnection[dict[str, object]]


def test_healthz_never_checks_dependencies(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_reports_the_database(client: TestClient) -> None:
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"database": True, "llm": True}


def test_readyz_is_503_when_the_database_is_down() -> None:
    settings = Settings(database_url="postgresql://localhost:1/nothing", llm_provider="fake")
    with TestClient(create_app(settings)) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["database"] is False


async def test_seed_loads_users_and_products(conn: Conn) -> None:
    rows = await (await conn.execute("SELECT count(*) AS n FROM data_products")).fetchall()
    assert rows[0]["n"] == 40
    owner = await (
        await conn.execute("SELECT owner, pii FROM data_products WHERE name = 'sales_es_monthly'")
    ).fetchone()
    assert owner == {"owner": "marta@finance", "pii": True}


async def test_audit_log_is_append_only(conn: Conn) -> None:
    await conn.execute(
        "INSERT INTO audit_log (user_id, conversation_id, kind, payload)"
        " VALUES ('u', 'c', 'k', '{}')"
    )
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        await conn.execute("UPDATE audit_log SET kind = 'changed'")
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        await conn.execute("DELETE FROM audit_log")


async def test_catalog_role_can_only_read_data_products(conn: Conn) -> None:
    async with conn.transaction():
        await conn.execute("SET LOCAL ROLE catalog_ro")
        rows = await (await conn.execute("SELECT count(*) AS n FROM data_products")).fetchall()
        assert rows[0]["n"] == 40
    async with conn.transaction(force_rollback=True):
        await conn.execute("SET LOCAL ROLE catalog_ro")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("SELECT * FROM users")


def test_settings_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from platform_copilot import config

    config.get_settings.cache_clear()
    monkeypatch.setenv("MAX_STEPS", "3")
    assert config.get_settings().max_steps == 3
    config.get_settings.cache_clear()


def test_create_app_reads_settings_when_none_are_given(monkeypatch: pytest.MonkeyPatch) -> None:
    from platform_copilot import config

    config.get_settings.cache_clear()
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    assert create_app().state.settings.llm_provider == "fake"
    config.get_settings.cache_clear()


async def test_seed_script_loads_the_configured_database(
    conn: Conn,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from platform_copilot import seed

    monkeypatch.setattr(seed, "get_settings", lambda: settings)
    await seed.main()
    assert "Loaded 40 data products and" in capsys.readouterr().out
