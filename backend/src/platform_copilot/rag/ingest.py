"""Load the markdown documents into PostgreSQL: one row per document, one per chunk."""

from pathlib import Path

import psycopg

from platform_copilot.embeddings import Embedder, to_pgvector
from platform_copilot.rag.chunking import chunk_document, parse_document


async def ingest_docs(
    conn: psycopg.AsyncConnection[object], docs_dir: Path, embedder: Embedder
) -> int:
    """Replace every document with the ones in `docs_dir`. Returns the number of chunks."""
    docs = [
        parse_document(path.name, path.read_text(encoding="utf-8"))
        for path in sorted(docs_dir.glob("*.md"))
    ]
    total = 0
    async with conn.transaction():
        await conn.execute("TRUNCATE documents RESTART IDENTITY CASCADE")
        for doc in docs:
            chunks = chunk_document(doc)
            vectors = await embedder.embed([chunk.content for chunk in chunks])
            cursor = await conn.execute(
                'INSERT INTO documents (path, title, "group") VALUES (%s, %s, %s) RETURNING id',
                (doc.path, doc.title, doc.group),
            )
            row = await cursor.fetchone()
            for chunk, vector in zip(chunks, vectors, strict=True):
                await conn.execute(
                    'INSERT INTO chunks (document_id, source_id, heading, content, "group",'
                    " embedding) VALUES (%s, %s, %s, %s, %s, %s::vector)",
                    (
                        _first(row),
                        chunk.source_id,
                        chunk.heading,
                        chunk.content,
                        doc.group,
                        to_pgvector(vector),
                    ),
                )
            total += len(chunks)
    return total


def _first(row: object) -> object:
    if isinstance(row, dict):
        return next(iter(row.values()))
    if isinstance(row, tuple):
        return row[0]
    raise ValueError("INSERT ... RETURNING returned no row")
