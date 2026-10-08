"""Bounded publication evidence without materializing a simulation ledger."""

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from thytrader.backtest.cost_attribution import BacktestCostAttribution
from thytrader.backtest.models import (
    BacktestDiagnostics,
    BacktestEvaluationWindow,
    BacktestPerformanceMetrics,
    BacktestSummary,
)
from thytrader.evaluation.models import CostAssumptions, FingerprintText


class BacktestProjection(BaseModel):
    """Authenticate publication evidence; full reads separately verify source artifacts."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    result_fingerprint: FingerprintText
    run_fingerprint: FingerprintText
    strategy_fingerprint: FingerprintText
    dataset_fingerprint: FingerprintText
    summary: BacktestSummary
    costs: CostAssumptions | None = None
    metrics: BacktestPerformanceMetrics | None = None
    cost_attribution: BacktestCostAttribution | None = None
    diagnostics: BacktestDiagnostics | None = None
    window: BacktestEvaluationWindow | None = None
    verification_scope: Literal["publication", "full_artifacts"] = "publication"
    warnings: tuple[str, ...] = ()


@runtime_checkable
class BacktestProjectionReader(Protocol):
    """Retrieve bounded, identity-checked publication projections in one round trip."""

    async def load_projections(
        self, result_fingerprints: tuple[str, ...]
    ) -> tuple[BacktestProjection, ...]:
        """Return at most 100 projections in requested order, failing on missing evidence."""
        ...
