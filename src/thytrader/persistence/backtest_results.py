"""Read-only contracts for immutable deterministic backtest results."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.backtest.models import BacktestResult, BacktestSummary


class BacktestResultUnavailableError(RuntimeError):
    """Signal that durable backtest-result storage is disabled or unreachable."""


class BacktestResultNotFoundError(LookupError):
    """Signal that one requested immutable result identity does not exist."""


class BacktestResultIntegrityError(RuntimeError):
    """Signal that a stored result failed canonical or source verification."""


class BacktestResultSummaryView:
    """One newest-first discovery row projected without the full trade ledger.

    The summary metrics are read from the canonical document's immutable
    ``summary`` block. Identity fields come from the indexed row columns so a
    list query never materializes a complete trade ledger or equity curve.
    """

    __slots__ = (
        "dataset_fingerprint",
        "engine_contract_version",
        "published_at",
        "result_fingerprint",
        "run_fingerprint",
        "strategy_fingerprint",
        "summary",
    )

    def __init__(
        self,
        *,
        result_fingerprint: str,
        run_fingerprint: str,
        strategy_fingerprint: str,
        dataset_fingerprint: str,
        engine_contract_version: str,
        published_at: datetime,
        summary: BacktestSummary,
    ) -> None:
        """Bind one verified identity row to its immutable summary block."""
        self.result_fingerprint = result_fingerprint
        self.run_fingerprint = run_fingerprint
        self.strategy_fingerprint = strategy_fingerprint
        self.dataset_fingerprint = dataset_fingerprint
        self.engine_contract_version = engine_contract_version
        self.published_at = published_at
        self.summary = summary


@runtime_checkable
class BacktestResultReader(Protocol):
    """Read immutable backtest results without granting any mutation authority."""

    async def list_summaries(
        self,
        *,
        run_fingerprint: str | None = None,
        strategy_fingerprint: str | None = None,
        dataset_fingerprint: str | None = None,
        limit: int,
        offset: int,
    ) -> tuple[BacktestResultSummaryView, ...]:
        """Return bounded newest-first summary rows for browser discovery."""
        ...

    async def load(self, result_fingerprint: str) -> BacktestResult:
        """Load and fully reverify one immutable result by its content identity."""
        ...


class DisabledBacktestResultStore:
    """Fail-closed read boundary used when durable result storage is unconfigured."""

    async def list_summaries(
        self,
        *,
        run_fingerprint: str | None = None,
        strategy_fingerprint: str | None = None,
        dataset_fingerprint: str | None = None,
        limit: int,
        offset: int,
    ) -> tuple[BacktestResultSummaryView, ...]:
        """Reject discovery so disabled persistence never looks like empty results."""
        del run_fingerprint, strategy_fingerprint, dataset_fingerprint, limit, offset
        raise BacktestResultUnavailableError("Backtest results are unavailable.")

    async def load(self, result_fingerprint: str) -> BacktestResult:
        """Reject inspection so disabled persistence never fabricates a result."""
        del result_fingerprint
        raise BacktestResultUnavailableError("Backtest results are unavailable.")
