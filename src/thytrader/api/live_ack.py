"""Explicit live-trading acknowledgement required on live HTTP mutations.

The CLI's ``--i-understand-live`` flag, the web UI live confirmations, and the
operator-chat understand-live checkbox all map onto the same request field,
``i_understand_live``. The API refuses live start, live resume, and live
discretionary place-order without it, so no HTTP client can arm or re-arm live
trading by omission. There is no backward-compatible default.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from thytrader.execution.models import DeploymentMode

LIVE_ACK_FIELD = "i_understand_live"
LIVE_ACK_REQUIRED_CODE = "live_acknowledgement_required"
LIVE_ACK_REQUIRED_DETAIL = (
    f"{LIVE_ACK_REQUIRED_CODE}: Live trading spends real money. Send "
    f"{LIVE_ACK_FIELD}=true only after the operator explicitly acknowledged live trading "
    "(CLI --i-understand-live, the UI live confirmation, or the chat understand-live box)."
)


def require_live_acknowledgement(mode: DeploymentMode, *, acknowledged: bool) -> None:
    """Raise HTTP 428 when a live mutation arrives without the explicit acknowledgement."""
    if mode is DeploymentMode.LIVE and not acknowledged:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail=LIVE_ACK_REQUIRED_DETAIL,
        )
