"""Explicit live-trading acknowledgement required on live HTTP mutations.

The CLI's ``--i-understand-live`` flag, the web UI live confirmations, and the
operator-chat understand-live checkbox all map onto the same request field,
``i_understand_live``. The API refuses live start, live resume, and live
discretionary place-order without it, so no HTTP client can arm or re-arm live
trading by omission. There is no backward-compatible default.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from thytrader.ops_contract import LIVE_ACK_REQUIRED_DETAIL
from thytrader.trading.models import DeploymentMode


def require_live_acknowledgement(mode: DeploymentMode, *, acknowledged: bool) -> None:
    """Raise HTTP 428 when a live mutation arrives without the explicit acknowledgement."""
    if mode is DeploymentMode.LIVE and not acknowledged:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail=LIVE_ACK_REQUIRED_DETAIL,
        )
