"""Shared fixtures. Tests use a real PostgreSQL (with pgvector) but never a real LLM or network."""

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from psycopg.rows import dict_row
from tokens import make_validator

from platform_copilot.config import Settings
from platform_copilot.db import Pool, make_pool
from platform_copilot.embeddings import FakeEmbedder
from platform_copilot.main import create_app
from platform_copilot.rag.ingest import ingest_docs
from platform_copilot.seed import SEED_DIR, load_users_and_products

ROOT = Path(__file__).resolve().parents[2]
TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://localhost/platform_copilot_test")
ADMIN_URL = os.environ.get("TEST_ADMIN_URL", "postgresql://localhost/postgres")
TABLES = "feedback, audit_log, access_requests, chunks, documents, data_products, users"


EXPORTER = InMemorySpanExporter()


@pytest.fixture(scope="session", autouse=True)
def tracing() -> None:
    """Spans go to memory, so tests can check what was traced. Set once: OpenTelemetry allows
    one global tracer provider per process."""
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
    trace.set_tracer_provider(provider)


@pytest.fixture
def spans() -> InMemorySpanExporter:
    EXPORTER.clear()
    return EXPORTER


@pytest.fixture(scope="session", autouse=True)
def fresh_database() -> None:
    db_name = TEST_DB_URL.rsplit("/", 1)[1]
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {db_name} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {db_name}")
    with psycopg.connect(TEST_DB_URL, autocommit=True) as conn:
        conn.execute((ROOT / "db" / "init.sql").read_text(encoding="utf-8").encode())


@pytest.fixture
async def conn() -> AsyncIterator[psycopg.AsyncConnection[dict[str, object]]]:
    """A clean database with the seeded users and products."""
    async with await psycopg.AsyncConnection.connect(
        TEST_DB_URL, autocommit=True, row_factory=dict_row
    ) as connection:
        await connection.execute(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE")
        await load_users_and_products(connection, SEED_DIR)
        yield connection


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url=TEST_DB_URL, llm_provider="fake", embed_provider="fake", search_fallback=False
    )


@pytest.fixture
def client(
    settings: Settings, conn: psycopg.AsyncConnection[dict[str, object]]
) -> Iterator[TestClient]:
    with TestClient(create_app(settings, token_validator=make_validator())) as test_client:
        yield test_client


@pytest.fixture
async def pool(
    conn: psycopg.AsyncConnection[dict[str, object]], settings: Settings
) -> AsyncIterator[Pool]:
    """An open pool on the clean database, with the docs ingested by the fake embedder."""
    await ingest_docs(conn, SEED_DIR / "docs", FakeEmbedder())
    db_pool = make_pool(settings.database_url)
    await db_pool.open()
    yield db_pool
    await db_pool.close()
