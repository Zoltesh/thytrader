"""Help text shared by several ``thytrader-runtime`` subcommand groups."""

from __future__ import annotations

_CONFIRM_HELP = (
    "Required for mutations unless YOLO covers that tier. Live start, live "
    "resume, and live place-order also require --i-understand-live. Publishing "
    "a risk policy requires --confirm only; it does not arm live trading. Live "
    "place-order, set-settings, and Coinbase credential set/clear never skip --confirm."
)
_LIVE_HELP = (
    "Required to start live trading, resume a live deployment, or place a live "
    "order. Sent to the API as i_understand_live=true. Live spends real money. "
    "YOLO never skips this flag."
)
