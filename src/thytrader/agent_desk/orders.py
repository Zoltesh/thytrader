"""The order an open decision becomes, and the live-only entry rules (ADR 0147, 0148)."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from typing import TYPE_CHECKING

from thytrader.agent_desk.ports import DeskError
from thytrader.execution.discretionary_request import DiscretionaryOrderRequest
from thytrader.trading.models import DeploymentMode, IntentOrigin, OrderKind, PositionSide

if TYPE_CHECKING:
    from thytrader.agent_desk.models import Desk, DeskDecision

_CENT = Decimal("0.01")


def check_live(decision: DeskDecision) -> None:
    """A live spot desk buys, and only with a limit: it never fills above its price."""
    if decision.side == "short":
        raise DeskError("DESK_SHORT_LIVE_UNSUPPORTED: a live spot desk can only buy.")
    if decision.entry != "limit":
        raise DeskError("DESK_LIVE_LIMIT_ONLY: a live desk enters with limit orders only.")


def order_request(
    decision: DeskDecision,
    desk: Desk,
    *,
    fingerprint: str,
    allocation: Decimal,
    committed: Decimal,
) -> DiscretionaryOrderRequest:
    """The discretionary order for an open decision: a fresh book in the desk's mode.

    Paper books start with the allocation as their cash and the account's fee rates; live
    orders are sized to the allocation and admitted by the desk's policy reservation.
    """
    market = decision.entry == "market"
    header = f"[desk {desk.name} | {desk.harness}{'/' + desk.model if desk.model else ''}] "
    live = desk.mode == "live"
    return DiscretionaryOrderRequest(
        mode=DeploymentMode.LIVE if live else DeploymentMode.PAPER,
        product_id=decision.product_id or "",
        entry_kind=OrderKind.MARKETABLE if market else OrderKind.POST_ONLY_LIMIT,
        stop_price=decision.stop_price or Decimal(0),
        take_profit_price=decision.take_profit_price or Decimal(0),
        origin=IntentOrigin.AGENT,
        # One answer to one briefing is one order, however often the answer is posted.
        idempotency_key=f"desk:{desk.desk_id}:{fingerprint.removeprefix('sha256:')[:32]}",
        timeframe=desk.timeframe,
        side=PositionSide.SHORT if decision.side == "short" else PositionSide.LONG,
        quantity=None,
        # Leave room for entry fees inside the book's own cash.
        quote_notional=(allocation * Decimal("0.98")).quantize(_CENT, rounding=ROUND_DOWN),
        limit_price=None if market else decision.limit_price,
        paper_starting_cash=None if live else allocation,
        paper_maker_fee_rate=None,
        paper_taker_fee_rate=None,
        note=(header + decision.thesis)[:4000],
        fresh_book=True,
        desk_id=desk.desk_id,
        desk_committed_quote=committed,
        # The reservation holds the whole allocation (fee headroom and sizing rounding
        # included), not only the order's notional.
        desk_allocation_quote=allocation,
    )
