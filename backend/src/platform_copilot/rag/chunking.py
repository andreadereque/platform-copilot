"""Split markdown documents into chunks that make sense on their own.

Each `## ` section becomes one chunk, prefixed with its heading path ("Title > Section").
Sections longer than `max_chars` are split on word boundaries with `overlap` characters repeated,
so a sentence cut in two still appears whole in one of the pieces.
"""

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Document:
    path: str
    title: str
    group: str
    body: str


@dataclass(frozen=True)
class Chunk:
    source_id: str
    heading: str
    content: str


class FrontMatterError(ValueError):
    pass


def parse_document(path: str, text: str) -> Document:
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if match is None:
        raise FrontMatterError(f"{path}: missing front matter")
    fields = dict(line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)
    fields = {key.strip(): value.strip() for key, value in fields.items()}
    if "title" not in fields or "group" not in fields:
        raise FrontMatterError(f"{path}: front matter needs title and group")
    return Document(path, fields["title"], fields["group"], match.group(2))


def chunk_document(doc: Document, max_chars: int = 800, overlap: int = 100) -> list[Chunk]:
    chunks: list[Chunk] = []
    for heading, text in _sections(doc.body):
        path = f"{doc.title} > {heading}"
        anchor = f"{doc.path}#{slugify(heading)}"
        pieces = _split(text, max_chars, overlap)
        for index, piece in enumerate(pieces):
            source_id = anchor
            if index > 0:
                source_id = f"{anchor}-{index + 1}"
            chunks.append(Chunk(source_id, path, f"{path}\n{piece}"))
    return chunks


def _sections(body: str) -> list[tuple[str, str]]:
    parts = re.split(r"^## +(.+)$", body, flags=re.MULTILINE)
    sections = []
    intro = parts[0].strip()
    if intro:
        sections.append(("Introducción", intro))
    for heading, text in zip(parts[1::2], parts[2::2], strict=True):
        sections.append((heading.strip(), text.strip()))
    return sections


def _split(text: str, max_chars: int, overlap: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    pieces = []
    start = 0
    while True:
        end = min(start + max_chars, len(text))
        if end < len(text):
            end = text.rfind(" ", start, end) + 1 or end
        pieces.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(end - overlap, start + 1)
    return pieces


def slugify(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")
