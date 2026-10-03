"""Capital-normalized performance without changing operational fill-ledger cash."""

from decimal import Decimal

from thytrader.execution.models import Deployment, DeploymentMode

_ZERO = Decimal("0")


def ledger_starting_equity(deployment: Deployment) -> Decimal:
    """Return the ledger's recorded opening balance, where exact zero is valid."""
    if deployment.initial_equity is not None:
        return deployment.initial_equity
    if deployment.paper_starting_cash is not None:
        return deployment.paper_starting_cash
    return _ZERO


def performance_capital(deployment: Deployment) -> Decimal | None:
    """Resolve a fixed positive budget; never silently replace an invalid pinned basis.

    Existing funded ledgers retain their opening balance. Zero-based live books use
    their allocation, or the first known sizing balance when unallocated. Writers pin
    this value before entries, so later allocation and venue changes cannot reset it.
    """
    pinned = deployment.performance_capital_quote
    if pinned is not None:
        return pinned if pinned.is_finite() and pinned > _ZERO else None
    candidates = (
        deployment.initial_equity,
        deployment.paper_starting_cash,
        deployment.allocated_capital,
        deployment.venue_available_quote if deployment.mode is DeploymentMode.LIVE else None,
    )
    return next(
        (
            value
            for value in candidates
            if value is not None and value.is_finite() and value > _ZERO
        ),
        None,
    )


def current_drawdown(deployment: Deployment, *, ledger_equity: Decimal) -> Decimal | None:
    """Measure current loss from the durable ledger peak on the pinned capital budget.

    Ledger peaks, including a negative peak after an explicit latch reset, remain in
    ledger units. A missing or exhausted performance basis is unknown, never zero.
    """
    capital = performance_capital(deployment)
    if capital is None:
        return None
    starting = ledger_starting_equity(deployment)
    high_water = deployment.high_water_mark_equity
    peak = max(starting if high_water is None else high_water, ledger_equity)
    funded_peak = capital + peak - starting
    if funded_peak <= _ZERO:
        return None
    return (peak - ledger_equity) / funded_peak
