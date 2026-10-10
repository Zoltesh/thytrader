"""Operator ``futures-account`` report: the latest CFM account mirror snapshot (ADR 0127).

Read-only. It shows what the worker's GET-only mirror last recorded: enablement, failed
reads, the balance summary (USD), open positions (contracts) and the margin window. Every
amount is a USD decimal string and is never added to a USDC or USDT amount; ``null`` is
unknown, never zero. Nothing here can order, close, sweep or change margin settings.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from thytrader import __version__
from thytrader.exchanges.futures_models import (
    SHARED_COLLATERAL_NOTE,
    FuturesAccountStoreUnavailableError,
    FuturesBalanceSummary,
    FuturesEnablement,
    margin_ratio,
)
from thytrader.operator.models import (
    PORTFOLIO_REDACTION,
    ComponentReport,
    OperatorEnvelope,
    ReportStatus,
    _FrozenModel,
)
from thytrader.operator.status import aggregate_status, recommend_next_action

if TYPE_CHECKING:
    from decimal import Decimal

    from thytrader.exchanges.futures_models import (
        FuturesAccountObservation,
        FuturesAccountSnapshotStore,
        FuturesMarginMeasure,
        FuturesPosition,
    )

# Three missed 60-second mirror cycles make the snapshot stale.
_STALE_AFTER = timedelta(seconds=180)


class FuturesMarginMeasurePayload(_FrozenModel):
    """One margin window's measures (USD strings)."""

    margin_window_type: str | None
    margin_level: str | None
    initial_margin: str | None
    maintenance_margin: str | None
    liquidation_buffer: str | None
    total_hold: str | None
    futures_buying_power: str | None


class FuturesBalancePayload(_FrozenModel):
    """The CFM balance summary; every amount is USD (``currency``)."""

    currency: Literal["USD"] = "USD"
    futures_buying_power: str | None
    total_usd_balance: str | None
    cbi_usd_balance: str | None
    cfm_usd_balance: str | None
    total_open_orders_hold_amount: str | None
    unrealized_pnl: str | None
    daily_realized_pnl: str | None
    initial_margin: str | None
    available_margin: str | None
    liquidation_threshold: str | None
    liquidation_buffer_amount: str | None
    liquidation_buffer_percentage: str | None
    total_pending_transfers_amount: str | None
    funding_pnl: str | None
    intraday_margin: FuturesMarginMeasurePayload | None
    overnight_margin: FuturesMarginMeasurePayload | None


class FuturesPositionPayload(_FrozenModel):
    """One open CFM position; ``number_of_contracts`` is in contracts, prices in USD."""

    product_id: str
    side: Literal["long", "short", "unknown"]
    number_of_contracts: str
    current_price: str | None
    avg_entry_price: str | None
    unrealized_pnl: str | None
    daily_realized_pnl: str | None
    expiration_time: datetime | None


class FuturesAccountPayload(_FrozenModel):
    """The latest mirror snapshot, or nothing before the first one.

    ``positions`` is ``null`` when the position read failed (unknown) and ``[]`` when the
    venue reported none. ``read_failures`` lists ``operation:reason`` tokens.
    ``margin_ratio`` is ``available_margin / liquidation_threshold`` (``null`` when either
    is unknown or the threshold is not positive, as on a flat account).
    ``collateral_note`` states that futures buying power is shared with the USDC spot
    balance.
    """

    observed_at: datetime | None
    age_seconds: int | None
    stale: bool | None
    enablement: Literal["enabled", "not_enabled", "unknown"] | None
    read_failures: tuple[str, ...]
    balance: FuturesBalancePayload | None
    positions: tuple[FuturesPositionPayload, ...] | None
    intraday_margin_setting: str | None
    margin_window_type: str | None
    margin_window_end_at: datetime | None
    intraday_killswitch_enabled: bool | None
    enrollment_killswitch_enabled: bool | None
    margin_ratio: str | None = None
    collateral_note: str = SHARED_COLLATERAL_NOTE
    orderable: Literal[False] = False


class FuturesAccountReport(OperatorEnvelope):
    """Read-only CFM futures account mirror."""

    report_kind: Literal["futures_account"] = "futures_account"
    payload: FuturesAccountPayload


_EMPTY = FuturesAccountPayload(
    observed_at=None,
    age_seconds=None,
    stale=None,
    enablement=None,
    read_failures=(),
    balance=None,
    positions=None,
    intraday_margin_setting=None,
    margin_window_type=None,
    margin_window_end_at=None,
    intraday_killswitch_enabled=None,
    enrollment_killswitch_enabled=None,
)
_DETAILS: dict[str, str] = {
    "OK": "The futures account mirror is current and every read succeeded.",
    "STORE_DISABLED": "Futures account storage is not configured (no database).",
    "STORE_UNAVAILABLE": "Futures account storage could not be read.",
    "FUTURES_MIRROR_NOT_RUN": (
        "The worker has not mirrored the futures account yet; it needs Coinbase credentials "
        "and a running worker."
    ),
    "FUTURES_MIRROR_STALE": "The newest futures account snapshot is older than 3 minutes.",
    "FUTURES_ACCOUNT_UNKNOWN": (
        "The balance summary could not be read, so futures enablement is unknown; see "
        "read_failures. Nothing is assumed."
    ),
    "FUTURES_READ_FAILURES": "Some futures account reads failed; their values are unknown.",
}


async def build_futures_account_report(
    store: FuturesAccountSnapshotStore | None, *, now: datetime | None = None
) -> FuturesAccountReport:
    """Assemble the report from the newest stored snapshot."""
    generated_at = now or datetime.now(UTC)
    if store is None:
        disabled = _component(ReportStatus.DEGRADED, "STORE_DISABLED")
        return _report(generated_at, _EMPTY, (disabled,))
    try:
        latest = await store.latest()
    except FuturesAccountStoreUnavailableError:
        unavailable = _component(ReportStatus.FAILED, "STORE_UNAVAILABLE")
        return _report(generated_at, _EMPTY, (unavailable,))
    if latest is None:
        return _report(
            generated_at, _EMPTY, (_component(ReportStatus.DEGRADED, "FUTURES_MIRROR_NOT_RUN"),)
        )
    age = generated_at - latest.observed_at
    payload = _payload(latest, age)
    return _report(generated_at, payload, _components(latest, stale=age > _STALE_AFTER))


def _components(latest: FuturesAccountObservation, *, stale: bool) -> tuple[ComponentReport, ...]:
    """Mirror freshness, then account-read outcome."""
    components: list[ComponentReport] = []
    if stale:
        components.append(_component(ReportStatus.DEGRADED, "FUTURES_MIRROR_STALE"))
    if latest.enablement is FuturesEnablement.UNKNOWN:
        components.append(_component(ReportStatus.DEGRADED, "FUTURES_ACCOUNT_UNKNOWN"))
    elif latest.read_failures:
        components.append(_component(ReportStatus.DEGRADED, "FUTURES_READ_FAILURES"))
    return tuple(components) or (_component(ReportStatus.HEALTHY, "OK"),)


def _payload(latest: FuturesAccountObservation, age: timedelta) -> FuturesAccountPayload:
    """Project one observation onto exact strings."""
    window = latest.margin_window
    return FuturesAccountPayload(
        observed_at=latest.observed_at,
        age_seconds=max(0, int(age.total_seconds())),
        stale=age > _STALE_AFTER,
        enablement=latest.enablement.value,
        read_failures=latest.read_failures,
        balance=None if latest.balance is None else _balance(latest.balance),
        positions=(
            None if latest.positions is None else tuple(_position(p) for p in latest.positions)
        ),
        intraday_margin_setting=latest.intraday_margin_setting,
        margin_window_type=None if window is None else window.margin_window_type,
        margin_window_end_at=None if window is None else window.end_time,
        intraday_killswitch_enabled=None if window is None else window.intraday_killswitch_enabled,
        enrollment_killswitch_enabled=(
            None if window is None else window.enrollment_killswitch_enabled
        ),
        margin_ratio=_text(None if latest.balance is None else margin_ratio(latest.balance)),
    )


_BALANCE_AMOUNTS = tuple(
    field.name
    for field in fields(FuturesBalanceSummary)
    if field.name not in {"intraday_margin", "overnight_margin"}
)


def _balance(balance: FuturesBalanceSummary) -> FuturesBalancePayload:
    """Project the balance summary."""
    fields_by_name: dict[str, object] = {
        name: _text(getattr(balance, name)) for name in _BALANCE_AMOUNTS
    }
    fields_by_name["intraday_margin"] = _measure(balance.intraday_margin)
    fields_by_name["overnight_margin"] = _measure(balance.overnight_margin)
    return FuturesBalancePayload.model_validate(fields_by_name)


def _measure(measure: FuturesMarginMeasure | None) -> FuturesMarginMeasurePayload | None:
    """Project one margin measure."""
    if measure is None:
        return None
    return FuturesMarginMeasurePayload(
        margin_window_type=measure.margin_window_type,
        margin_level=measure.margin_level,
        initial_margin=_text(measure.initial_margin),
        maintenance_margin=_text(measure.maintenance_margin),
        liquidation_buffer=_text(measure.liquidation_buffer),
        total_hold=_text(measure.total_hold),
        futures_buying_power=_text(measure.futures_buying_power),
    )


def _position(position: FuturesPosition) -> FuturesPositionPayload:
    """Project one position."""
    return FuturesPositionPayload(
        product_id=position.product_id,
        side=position.side.value,
        number_of_contracts=format(position.number_of_contracts, "f"),
        current_price=_text(position.current_price),
        avg_entry_price=_text(position.avg_entry_price),
        unrealized_pnl=_text(position.unrealized_pnl),
        daily_realized_pnl=_text(position.daily_realized_pnl),
        expiration_time=position.expiration_time,
    )


def _text(value: Decimal | None) -> str | None:
    """Exact decimal string, or ``None`` when unknown."""
    return None if value is None else format(value, "f")


def _component(status: ReportStatus, reason_code: str) -> ComponentReport:
    """One component with its fixed detail."""
    return ComponentReport(
        name="futures_account", status=status, reason_code=reason_code, detail=_DETAILS[reason_code]
    )


def _report(
    generated_at: datetime,
    payload: FuturesAccountPayload,
    components: tuple[ComponentReport, ...],
) -> FuturesAccountReport:
    """Wrap the payload; balances are shown, identifiers and secrets never are."""
    return FuturesAccountReport(
        application_version=__version__,
        generated_at=generated_at,
        overall_status=aggregate_status(components),
        components=components,
        redaction=PORTFOLIO_REDACTION,
        recommended_next_action=recommend_next_action(components),
        payload=payload,
    )
