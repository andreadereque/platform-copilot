"""Keyset (cursor) pagination, shared by the audit log and the access requests.

Offset pagination (`LIMIT 20 OFFSET 10000`) makes PostgreSQL read and throw away 10 000 rows,
and rows inserted while someone is paging shift every later page (duplicates or gaps).
Keyset pagination remembers the last row seen instead and asks for the rows "after" it:

    WHERE (created_at, id) < (:last_created_at, :last_id)
    ORDER BY created_at DESC, id DESC
    LIMIT :limit + 1

With an index on (created_at DESC, id DESC) every page costs the same, page 1 or page 500.
`id` breaks ties between rows created in the same microsecond. Asking for one extra row tells
us whether there is a next page without a separate COUNT(*).

The cursor is opaque to the client: base64 of the last row's key. Clients must not build or
parse it, so we can change what is inside without breaking them.
"""

import base64
import binascii
import json
from datetime import datetime
from typing import NamedTuple

from pydantic import BaseModel


class InvalidCursorError(ValueError):
    pass


class Key(NamedTuple):
    """Position of a row in the (created_at DESC, id DESC) order."""

    created_at: datetime
    id: int


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None


def encode_cursor(key: Key) -> str:
    raw = json.dumps([key.created_at.isoformat(), key.id]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> Key:
    """Turn a cursor back into a key. Anything malformed is an InvalidCursorError (HTTP 400)."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        created_at, row_id = json.loads(base64.urlsafe_b64decode(padded))
        key = Key(datetime.fromisoformat(created_at), int(row_id))
    except (binascii.Error, ValueError, TypeError) as error:
        raise InvalidCursorError("Invalid cursor") from error
    if key.created_at.tzinfo is None:
        raise InvalidCursorError("Invalid cursor")
    return key


def make_page[T](rows: list[T], keys: list[Key], limit: int) -> Page[T]:
    """`rows` were fetched with LIMIT limit + 1: the extra row only says there is more."""
    if len(rows) <= limit:
        return Page(items=rows, next_cursor=None)
    return Page(items=rows[:limit], next_cursor=encode_cursor(keys[limit - 1]))
