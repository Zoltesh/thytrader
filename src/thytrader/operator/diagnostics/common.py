"""Strategy-clock helpers shared by several operator diagnostics groups."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.market_data.models import as_dataset_timeframe, parse_candle_interval

if TYPE_CHECKING:
    from thytrader.execution.models import Deployment
    from thytrader.market_data.products import SpotQuoteCurrency
    from thytrader.operator.models import SupportedTimeframe
    from thytrader.operator.service import OperatorDiagnostics


async def _runtime_timeframe(
    diagnostics: OperatorDiagnostics, deployment: Deployment
) -> SupportedTimeframe:
    """Prefer a stored book clock; otherwise copy the published strategy clock."""
    stored = _supported_clock(deployment.timeframe)
    if stored is not None:
        return stored
    if deployment.strategy_fingerprint is None:
        return "1h"
    return await _strategy_timeframe(diagnostics, deployment.strategy_fingerprint)


async def _strategy_timeframe(
    diagnostics: OperatorDiagnostics, fingerprint: str
) -> SupportedTimeframe:
    """Copy the published strategy clock; default 1h when it cannot be loaded."""
    clock, _quote = await _strategy_clock_and_quote(diagnostics, fingerprint)
    return clock


async def _strategy_clock_and_quote(
    diagnostics: OperatorDiagnostics, fingerprint: str
) -> tuple[SupportedTimeframe, str]:
    """Copy the published strategy clock and quote currency; USD is the fallback."""
    load = getattr(diagnostics.publications, "load", None)
    if not callable(load):
        return "1h", "USD"
    try:
        published = await load(fingerprint)
    except Exception:  # noqa: BLE001 - missing strategy evidence keeps safe defaults.
        return "1h", "USD"
    clock = _supported_clock(published.definition.timeframe)
    if clock is None:
        return "1h", "USD"
    return clock, published.definition.instrument.quote_currency


async def _strategy_clock_and_published_quote(
    diagnostics: OperatorDiagnostics, fingerprint: str
) -> tuple[SupportedTimeframe, SpotQuoteCurrency | None]:
    """Copy the published clock and quote, or ``None`` quote when unpublished.

    The quote currency is provenance, not decoration: PnL amounts must not be
    relabeled when the published instrument cannot be loaded.
    """
    load = getattr(diagnostics.publications, "load", None)
    if not callable(load):
        return "1h", None
    try:
        published = await load(fingerprint)
    except Exception:  # noqa: BLE001 - unprovable currency stays None, never a guess.
        return "1h", None
    clock = _supported_clock(published.definition.timeframe)
    if clock is None:
        return "1h", None
    return clock, published.definition.instrument.quote_currency


def _supported_clock(value: str | None) -> SupportedTimeframe | None:
    """Return an ingested venue clock token, otherwise omit the field."""
    if value is None or value == "":
        return None
    try:
        interval = parse_candle_interval(value)
    except ValueError:
        return None
    if not interval.execution_supported:
        return None
    return as_dataset_timeframe(interval)
