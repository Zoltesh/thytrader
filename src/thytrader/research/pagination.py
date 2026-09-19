"""Opaque offset cursors for bounded research listing pages."""

from __future__ import annotations

import base64


def encode_offset_cursor(offset: int) -> str:
    """Encode one zero-based listing offset as an opaque cursor."""
    if offset < 0:
        raise ValueError("Invalid pagination cursor.")
    payload = f"offset:{offset}"
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_offset_cursor(cursor: str) -> int:
    """Decode one listing cursor into a zero-based offset."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        prefix, value = raw.split(":", 1)
        offset = int(value)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("Invalid pagination cursor.") from error
    if prefix != "offset" or offset < 0:
        raise ValueError("Invalid pagination cursor.")
    return offset
