import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import psycopg
import pytest

from platform_copilot.config import Settings
from platform_copilot.db import Pool, make_pool
from platform_copilot.embeddings import (
    EmbeddingError,
    FakeEmbedder,
    OllamaEmbedder,
    build_embedder,
    to_pgvector,
)
from platform_copilot.rag.chunking import (
    Document,
    FrontMatterError,
    chunk_document,
    parse_document,
    slugify,
)
from platform_copilot.rag.ingest import _first, ingest_docs
from platform_copilot.rag.search import hybrid_search
from platform_copilot.seed import SEED_DIR
from platform_copilot.tools.base import ToolContext
from platform_copilot.tools.search_docs import TOOL, SearchDocsArgs
from platform_copilot.users import UnknownUserError, User, get_user, list_users

Conn = psycopg.AsyncConnection[dict[str, object]]
LAURA = User("laura@sales", "Laura Gil", ["sales", "public"])


# ---------- chunking ----------


def test_parse_document_reads_front_matter() -> None:
    doc = parse_document("a.md", "---\ntitle: A title\ngroup: sales\n---\n## One\nText")
    assert (doc.title, doc.group, doc.body) == ("A title", "sales", "## One\nText")


@pytest.mark.parametrize("text", ["no front matter", "---\ntitle: only title\n---\nbody"])
def test_parse_document_rejects_bad_front_matter(text: str) -> None:
    with pytest.raises(FrontMatterError):
        parse_document("bad.md", text)


def test_each_section_is_a_chunk_with_its_heading_path() -> None:
    doc = Document(
        "policy.md", "Política", "public", "Intro.\n## Datos con PII\nTexto PII.\n## Otro\nX"
    )
    chunks = chunk_document(doc)
    assert [c.source_id for c in chunks] == [
        "policy.md#introduccion",
        "policy.md#datos-con-pii",
        "policy.md#otro",
    ]
    assert chunks[1].heading == "Política > Datos con PII"
    assert chunks[1].content == "Política > Datos con PII\nTexto PII."


def test_long_sections_are_split_with_overlap() -> None:
    words = " ".join(f"palabra{i:03d}" for i in range(200))
    doc = Document("long.md", "Largo", "public", f"## Sección\n{words}")
    chunks = chunk_document(doc, max_chars=300, overlap=50)
    assert len(chunks) > 3
    assert chunks[1].source_id == "long.md#seccion-2"
    first_tail = chunks[0].content.split()[-1]
    assert first_tail in chunks[1].content  # the overlap repeats the end of the previous piece
    assert all(len(c.content) <= 300 + len("Largo > Sección\n") for c in chunks)


def test_split_without_spaces_still_progresses() -> None:
    doc = Document("x.md", "X", "public", "## S\n" + "a" * 50)
    assert len(chunk_document(doc, max_chars=20, overlap=5)) > 2


def test_slugify_removes_accents_and_symbols() -> None:
    assert slugify("Política de acceso: ¡PII!") == "politica-de-acceso-pii"


# ---------- embeddings ----------


async def test_fake_embedder_is_deterministic_and_normalised() -> None:
    embedder = FakeEmbedder()
    [a, b, empty] = await embedder.embed(["acceso PII", "acceso PII", "!!"])
    assert a == b
    assert abs(sum(v * v for v in a) - 1) < 1e-9
    assert set(empty) == {0.0}


async def test_ollama_embedder_posts_the_texts() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})

    embedder = OllamaEmbedder("http://ollama/", "nomic", transport=httpx.MockTransport(handler))
    assert await embedder.embed(["hola"]) == [[0.1, 0.2]]
    assert seen == {"model": "nomic", "input": ["hola"]}


async def test_ollama_embedder_raises_a_clear_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    embedder = OllamaEmbedder("http://ollama", "nomic", transport=httpx.MockTransport(handler))
    with pytest.raises(EmbeddingError, match="Cannot embed"):
        await embedder.embed(["hola"])


def test_build_embedder_follows_settings() -> None:
    assert isinstance(build_embedder(Settings(embed_provider="fake")), FakeEmbedder)
    assert isinstance(build_embedder(Settings(embed_provider="ollama")), OllamaEmbedder)


def test_to_pgvector_format() -> None:
    assert to_pgvector([0.5, -1.0]) == "[0.500000,-1.000000]"


# ---------- ingest + search ----------


@pytest.fixture
async def pool(conn: Conn, settings: Settings) -> AsyncIterator[Pool]:
    await ingest_docs(conn, SEED_DIR / "docs", FakeEmbedder())
    db_pool = make_pool(settings.database_url)
    await db_pool.open()
    yield db_pool
    await db_pool.close()


async def test_ingest_loads_every_document(conn: Conn, pool: Pool) -> None:
    docs = await (await conn.execute("SELECT count(*) AS n FROM documents")).fetchone()
    chunks = await (await conn.execute("SELECT count(*) AS n FROM chunks")).fetchone()
    assert docs == {"n": 21}
    assert chunks == {"n": 59}


async def search(pool: Pool, query: str, groups: list[str]) -> list[str]:
    [vector] = await FakeEmbedder().embed([query])
    hits = await hybrid_search(pool, query, vector, groups, top_k=4)
    return [hit.source_id for hit in hits]


async def test_hybrid_search_finds_the_policy_section(pool: Pool) -> None:
    ids = await search(pool, "quién aprueba el acceso a datos con PII", ["public"])
    assert "access-policy.md#datos-con-pii" in ids[:2]


async def test_exact_names_are_found_by_full_text(pool: Pool) -> None:
    ids = await search(pool, "sales_targets_2026", ["sales", "public"])
    assert ids[0].startswith("sales-")


async def test_search_never_returns_chunks_from_other_groups(pool: Pool) -> None:
    ids = await search(pool, "salarios de empleados restringidos", ["sales", "public"])
    assert not any(source.startswith("hr-") for source in ids)
    hr_ids = await search(pool, "salarios de empleados restringidos", ["hr", "public"])
    assert hr_ids[0].startswith("hr-")


async def test_search_docs_tool(pool: Pool, settings: Settings) -> None:
    ctx = ToolContext(pool, LAURA, FakeEmbedder(), settings, "c1")
    output = await TOOL.run(SearchDocsArgs(query="calendario de guardias", top_k=3), ctx)
    assert output.summary == "3 chunks"
    assert len(output.sources) == 3
    assert not any(s.startswith(("oncall", "runbook")) for s in output.sources)  # platform only
    assert output.content["chunks"][0]["source_id"] == output.sources[0]
    assert TOOL.schema()["function"]["parameters"]["required"] == ["query"]


def test_first_reads_tuples_and_dicts() -> None:
    assert _first((7,)) == 7
    assert _first({"id": 8}) == 8
    with pytest.raises(ValueError, match="no row"):
        _first(None)


async def test_ingest_with_tuple_rows(settings: Settings, conn: Conn, tmp_path: Path) -> None:
    (tmp_path / "one.md").write_text("---\ntitle: Uno\ngroup: public\n---\n## A\nTexto")
    async with await psycopg.AsyncConnection.connect(settings.database_url) as plain:
        assert await ingest_docs(plain, tmp_path, FakeEmbedder()) == 1


# ---------- users ----------


async def test_users(pool: Pool) -> None:
    users = await list_users(pool)
    assert [u.id for u in users][:2] == ["ana@hr", "dev@platform"]
    assert (await get_user(pool, "laura@sales")).groups == ["sales", "public"]
    with pytest.raises(UnknownUserError):
        await get_user(pool, "nobody")
