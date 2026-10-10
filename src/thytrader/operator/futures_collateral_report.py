"""The ``futures_collateral`` block of the operator risk report (ADR 0129, slice P1-2a).

It shows what the live shared-collateral gate sees and does: the classified CFM futures
state, the USD figures behind it, the policy's declared reserve and haircut, and the effect
on new live USD/USDC spot entries. It reuses the gate's own decision so the report and the
gate cannot disagree. Every amount stays in its own currency; nothing is summed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from thytrader.exchanges.futures_models import SHARED_COLLATERAL_NOTE
from thytrader.operator.models import _FrozenModel
from thytrader.risk.futures_collateral import (
    COLLATERAL_LINKED_QUOTES,
    FuturesCollateralState,
    collateral_verdict,
    load_futures_collateral,
)
from thytrader.risk.gate_common import ProposedEntry
from thytrader.risk.models import RiskReasonCode
from thytrader.trading.models import DeploymentMode

if TYPE_CHECKING:
    from thytrader.exchanges.futures_models import FuturesAccountSnapshotStore
    from thytrader.risk.models import RiskPolicyDefinition

CollateralEffect = Literal[
    "none",
    "not_linked",
    "live_spot_entries_denied",
    "reserve_short_entries_denied",
    "reserve_withheld",
]


class FuturesCollateralPayload(_FrozenModel):
    """The gate's view of the shared USDC collateral pool.

    ``state`` is ``absent``, ``idle``, ``in_use`` or ``unknown``. USD figures come from the
    newest CFM mirror snapshot; ``reserve_quote`` is in the policy quote. ``effect`` is what
    the gate does to new live entries in the policy quote: ``none`` (idle or absent),
    ``not_linked`` (a USDT policy), ``live_spot_entries_denied``,
    ``reserve_short_entries_denied`` or ``reserve_withheld``.
    """

    state: Literal["absent", "idle", "in_use", "unknown"]
    cause: str
    observed_at: datetime | None
    initial_margin_usd: str | None
    open_orders_hold_usd: str | None
    position_count: int | None
    policy_quote: str
    reserve_quote: str | None
    peg_haircut: str | None
    effect: CollateralEffect
    reason_code: str | None
    collateral_note: str = SHARED_COLLATERAL_NOTE


async def futures_collateral_payload(
    store: FuturesAccountSnapshotStore | None,
    policy: RiskPolicyDefinition,
    *,
    now: datetime | None = None,
) -> FuturesCollateralPayload:
    """Classify the CFM account and evaluate the gate for a policy-quote live entry."""
    as_of = now or datetime.now(UTC)
    evidence = await load_futures_collateral(store, mode=DeploymentMode.LIVE, as_of=as_of)
    if evidence is None:  # Only paper returns None; this call is always live.
        raise AssertionError("live collateral evidence was not produced")
    quote = policy.quote_currency
    futures = policy.futures
    effect: CollateralEffect = "none"
    reason: str | None = None
    if quote not in COLLATERAL_LINKED_QUOTES:
        effect = "not_linked"
    else:
        probe = ProposedEntry(product_id=f"BTC-{quote}", strategy_id=None, notional=Decimal(1))
        reserve = _reserve(policy)
        # Probe cash equal to the reserve: only the reserve-versus-margin check can deny.
        verdict, _cash = collateral_verdict(
            policy,
            mode=DeploymentMode.LIVE,
            proposed=probe,
            evidence=evidence,
            live_quote_cash=reserve,
        )
        if verdict is None:
            effect = (
                "reserve_withheld" if evidence.state is FuturesCollateralState.IN_USE else "none"
            )
        else:
            reason = verdict.reason_code.value
            effect = (
                "reserve_short_entries_denied"
                if verdict.reason_code is RiskReasonCode.FUTURES_COLLATERAL_RESERVE_SHORT
                else "live_spot_entries_denied"
            )
    return FuturesCollateralPayload(
        state=evidence.state.value,
        cause=evidence.cause,
        observed_at=evidence.observed_at,
        initial_margin_usd=_text(evidence.initial_margin_usd),
        open_orders_hold_usd=_text(evidence.open_orders_hold_usd),
        position_count=evidence.position_count,
        policy_quote=quote,
        reserve_quote=None if futures is None else futures.live_spot_collateral_reserve_quote,
        peg_haircut=None if futures is None else futures.peg_haircut,
        effect=effect,
        reason_code=reason,
    )


def _reserve(policy: RiskPolicyDefinition) -> Decimal | None:
    """The declared L3 reserve in the policy quote, if any."""
    futures = policy.futures
    if futures is None or futures.live_spot_collateral_reserve_quote is None:
        return None
    return Decimal(futures.live_spot_collateral_reserve_quote)


def _text(value: Decimal | None) -> str | None:
    """Exact decimal string or ``None``."""
    return None if value is None else format(value, "f")
