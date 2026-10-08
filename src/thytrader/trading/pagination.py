"""Opaque cursors for cursor-paginated deployment ledger reads."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from uuid import UUID


def encode_cursor(*, filled_at: datetime, row_id: UUID) -> str:
    """Encode one descending pagination key as an opaque cursor."""
    aware = filled_at if filled_at.tzinfo is not None else filled_at.replace(tzinfo=UTC)
    payload = f"{aware.astimezone(UTC).isoformat()}|{row_id}"
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Decode one fill or order cursor; raise ValueError when malformed."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        filled_at_text, row_id_text = raw.split("|", 1)
        return datetime.fromisoformat(filled_at_text), UUID(row_id_text)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("Invalid pagination cursor.") from error


def encode_order_cursor(*, created_at: datetime, row_id: UUID) -> str:
    """Encode one order row cursor using created_at ordering."""
    return encode_cursor(filled_at=created_at, row_id=row_id)


def decode_order_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Decode one order cursor."""
    return decode_cursor(cursor)
