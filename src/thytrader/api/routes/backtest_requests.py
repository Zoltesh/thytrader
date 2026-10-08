"""Request validation and redacted HTTP errors shared by the backtest routes.

Validates result fingerprints, fingerprint filters and opaque pagination cursors, stops
work once the client disconnects, and builds the signal-trace 503 envelope.
"""

from __future__ import annotations

import re

from fastapi import HTTPException, Request, status

from thytrader.research.pagination import decode_offset_cursor

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def _raise_client_disconnected() -> None:
    """Stop building a large backtest payload after the client disconnects."""
    raise HTTPException(
        status_code=status.HTTP_499_CLIENT_CLOSED_REQUEST,
        detail={"code": "backtest_invalid", "message": "Client disconnected."},
    )


async def _require_connected(http_request: Request) -> None:
    """Avoid fetching further evidence after the requesting client disconnects."""
    if await http_request.is_disconnected():
        _raise_client_disconnected()


def _fingerprint_or_none(value: str | None) -> str | None:
    """Validate one optional fingerprint filter, rejecting malformed identities."""
    if value is None:
        return None
    if _FINGERPRINT_PATTERN.fullmatch(value) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "backtest_invalid", "message": "Fingerprint filter is malformed."},
        )
    return value


def _list_offset(*, offset: int, cursor: str | None) -> int:
    """Prefer an opaque cursor when present; otherwise use the numeric offset."""
    if cursor is None:
        return offset
    try:
        return decode_offset_cursor(cursor)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "backtest_invalid", "message": "Pagination cursor is malformed."},
        ) from None


def _trace_unavailable(message: str) -> HTTPException:
    """Build the 503 envelope for a trace that could not be re-evaluated or verified."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "signal_trace_unavailable", "message": message},
    )
