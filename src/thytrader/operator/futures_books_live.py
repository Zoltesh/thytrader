"""Read-only live book facts, scoped separately from modeled USD book economics."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from thytrader.decimal_text import canonical_decimal
from thytrader.execution.futures_live import managed_live_futures
from thytrader.execution.futures_visibility import visible_position_comparison
from thytrader.execution.futures_visibility_probe import visibility_probe
from thytrader.operator.models import _FrozenModel
from thytrader.risk.futures_entry_probe import live_probe_verdicts
from thytrader.risk.futures_reserve import reserve_coverage

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.exchanges.futures_models import FuturesAccountObservation
    from thytrader.execution.futures_live import FuturesLiveAccount
    from thytrader.market_data.futures_observations import FuturesInstrumentObservation
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.trading.futures_book import BoundFuturesContract
    from thytrader.trading.models import DeploymentSnapshot


class LiveFuturesBookPayload(_FrozenModel):
    """Timestamped account facts; account totals are never attributed to one book."""

    scope: Literal["cfm_account"] = "cfm_account"
    observed_at: datetime | None
    evidence_status: Literal["fresh", "stale", "unknown"]
    venue_contracts: str | None
    match_state: Literal["managed_matched", "managed_mismatch", "unknown"]
    match_reason: str | None
    margin_ratio: str | None
    available_margin_usd: str | None
    buying_power_usd: str | None
    initial_margin_usd: str | None
    reserve_currency: str | None
    reserve_quote: str | None
    reserve_required_usd: str | None
    reserve_headroom_quote: str | None
    reserve_covers_current_margin: bool | None
    funding_accrued_usd: str
    venue_funding_pnl_usd: str | None
    venue_funding_scope: Literal["account_counter_not_settlement"] = (
        "account_counter_not_settlement"
    )
    funding_drift_status: Literal["unknown"] = "unknown"
    funding_drift_reason: Literal["SETTLEMENT_PROVENANCE_UNAVAILABLE"] = (
        "SETTLEMENT_PROVENANCE_UNAVAILABLE"
    )
    entry_probe: Literal["one_contract_long_and_short_not_order_authority"] = (
        "one_contract_long_and_short_not_order_authority"
    )


def live_payload(
    snapshot: DeploymentSnapshot,
    *,
    account: FuturesLiveAccount | None,
    observation: FuturesAccountObservation | None,
    policy: RiskPolicyDefinition | None,
    books: Sequence[DeploymentSnapshot],
    now: datetime,
) -> LiveFuturesBookPayload:
    """Project current evidence without promoting stale counters to settlement proof."""
    venue = None if account is None else account.venue
    fresh = venue is not None and venue.is_fresh(now)
    sample = visible_position_comparison(snapshot, account, now=now)
    owners = [
        book
        for book in books
        if managed_live_futures(book)
        and book.deployment.product_id == snapshot.deployment.product_id
    ]
    if (
        len(owners) != 1
        or venue is None
        or venue.external_order_products is None
        or snapshot.deployment.product_id in venue.external_order_products
    ):
        sample = None
    ratio = None
    if fresh and account is not None:
        available, threshold = account.available_margin_usd, account.liquidation_threshold_usd
        if (
            available is not None
            and threshold is not None
            and available.is_finite()
            and threshold.is_finite()
            and threshold > 0
        ):
            ratio = available / threshold
    initial = None if not fresh or venue is None else venue.initial_margin_usd
    coverage = reserve_coverage(policy, initial)
    count = (
        None
        if not fresh or venue is None or venue.positions is None
        else venue.positions.get(snapshot.deployment.product_id, Decimal(0))
    )
    return LiveFuturesBookPayload(
        observed_at=None if venue is None else venue.observed_at,
        evidence_status="unknown" if venue is None else ("fresh" if fresh else "stale"),
        venue_contracts=_text(count),
        match_state="unknown"
        if sample is None
        else ("managed_matched" if sample.matches else "managed_mismatch"),
        match_reason="POSITION_COMPARISON_UNPROVEN" if sample is None else None,
        margin_ratio=_text(ratio),
        available_margin_usd=_text(
            None if not fresh or account is None else account.available_margin_usd
        ),
        buying_power_usd=_text(None if not fresh or venue is None else venue.buying_power_usd),
        initial_margin_usd=_text(initial),
        reserve_currency=coverage.currency,
        reserve_quote=coverage.reserve_quote,
        reserve_required_usd=_text(coverage.required_usd),
        reserve_headroom_quote=_text(coverage.headroom_quote),
        reserve_covers_current_margin=coverage.covers,
        funding_accrued_usd=canonical_decimal(
            sum((flow.amount for flow in snapshot.funding), Decimal(0))
        ),
        venue_funding_pnl_usd=_text(
            None
            if not fresh or observation is None or observation.balance is None
            else observation.balance.funding_pnl
        ),
    )


def live_entry_blocks(
    snapshot: DeploymentSnapshot,
    *,
    binding: BoundFuturesContract | None,
    observation: FuturesInstrumentObservation | None,
    account: FuturesLiveAccount | None,
    policy: RiskPolicyDefinition | None,
    books: Sequence[DeploymentSnapshot],
    price: Decimal | None,
    now: datetime,
) -> tuple[str, ...]:
    """Run actual P2-3 admission for minimum whole-contract probes in both directions."""
    if policy is None:
        return ("FUTURES_POLICY_UNKNOWN",)
    probe = visibility_probe(
        snapshot, binding=binding, observation=observation, price=price, now=now
    )
    verdicts = live_probe_verdicts(
        policy,
        book=snapshot,
        probe=probe,
        snapshots=books,
        venue=None if account is None else account.venue,
        now=now,
    )
    return tuple(dict.fromkeys(verdict.reason_code.value for verdict in verdicts))


def _text(value: Decimal | None) -> str | None:
    """Finite exact decimals only; invalid account facts remain unknown."""
    return None if value is None or not value.is_finite() else canonical_decimal(value)
