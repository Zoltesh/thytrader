"""Fail-closed local evidence lookup for experiential training.

Unresolved pointers refuse training. Missing candles are never interpolated.
This module never calls Coinbase.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import UUID

from thytrader.execution.models import DeploymentMode
from thytrader.execution.store import ExecutionStoreError
from thytrader.market_data.datasets import DatasetStoreError
from thytrader.memory.models import EvidenceKind
from thytrader.persistence.backtest_results import (
    BacktestResultNotFoundError,
    BacktestResultUnavailableError,
)

if TYPE_CHECKING:
    from thytrader.execution.store import ExecutionStore
    from thytrader.market_data.datasets import DatasetStore
    from thytrader.persistence.backtest_results import BacktestResultReader


@runtime_checkable
class EvidenceResolver(Protocol):
    """Prove that one journal or pattern pointer names local immutable evidence."""

    async def exists(self, kind: EvidenceKind, evidence_id: str) -> bool:
        """Return True only when the named local artifact is present."""
        ...


class StaticEvidenceResolver:
    """Test double with an explicit allowlist of local evidence identities."""

    def __init__(self, known: frozenset[tuple[EvidenceKind, str]]) -> None:
        """Bind the known (kind, id) pairs."""
        self._known = known

    async def exists(self, kind: EvidenceKind, evidence_id: str) -> bool:
        """Return whether the pair was registered as local evidence."""
        return (kind, evidence_id) in self._known


class LocalEvidenceResolver:
    """Resolve evidence against backtests, datasets, and execution records."""

    def __init__(
        self,
        *,
        backtests: BacktestResultReader,
        datasets: DatasetStore,
        execution: ExecutionStore,
    ) -> None:
        """Bind read-only local stores. None of these submit venue orders."""
        self._backtests = backtests
        self._datasets = datasets
        self._execution = execution

    async def exists(self, kind: EvidenceKind, evidence_id: str) -> bool:
        """Fail closed when the cited local artifact cannot be loaded."""
        if kind is EvidenceKind.NONE:
            return False
        if kind is EvidenceKind.BACKTEST:
            return await _backtest_exists(self._backtests, evidence_id)
        if kind is EvidenceKind.RESEARCH:
            return await _research_exists(self._backtests, evidence_id)
        if kind is EvidenceKind.MARKET_DATA:
            return _dataset_exists(self._datasets, evidence_id)
        if kind is EvidenceKind.DEPLOYMENT:
            return await _deployment_exists(self._execution, evidence_id)
        if kind is EvidenceKind.PAPER_FILL:
            return await _fill_exists(self._execution, evidence_id, DeploymentMode.PAPER)
        if kind is EvidenceKind.LIVE_FILL:
            return await _fill_exists(self._execution, evidence_id, DeploymentMode.LIVE)
        return False


async def _backtest_exists(store: BacktestResultReader, evidence_id: str) -> bool:
    """Load one result fingerprint or treat missing storage as absent."""
    try:
        await store.load(evidence_id)
    except BacktestResultUnavailableError as error:
        raise ExperientialEvidenceError(
            "backtest evidence storage is unavailable; training is fail-closed"
        ) from error
    except (BacktestResultNotFoundError, ValueError):
        return False
    return True


async def _research_exists(store: BacktestResultReader, evidence_id: str) -> bool:
    """Treat a research run as present when a child result summary exists."""
    try:
        rows = await store.list_summaries(run_fingerprint=evidence_id, limit=1, offset=0)
    except BacktestResultUnavailableError as error:
        raise ExperientialEvidenceError(
            "research evidence storage is unavailable; training is fail-closed"
        ) from error
    except ValueError:
        return False
    return len(rows) > 0


def _dataset_exists(store: DatasetStore, evidence_id: str) -> bool:
    """Load one complete-only dataset fingerprint without interpolating candles."""
    try:
        store.load_manifest(evidence_id)
    except (DatasetStoreError, OSError, ValueError):
        return False
    return True


async def _deployment_exists(store: ExecutionStore, evidence_id: str) -> bool:
    """Load one deployment identity from execution storage."""
    try:
        deployment_id = UUID(evidence_id)
    except ValueError:
        return False
    try:
        await store.get_deployment(deployment_id)
    except ExecutionStoreError:
        return False
    return True


async def _fill_exists(
    store: ExecutionStore,
    evidence_id: str,
    mode: DeploymentMode,
) -> bool:
    """Scan local fills for one id whose deployment mode matches the evidence kind."""
    try:
        fill_id = UUID(evidence_id)
    except ValueError:
        return False
    try:
        deployments = await store.list_deployments()
    except ExecutionStoreError:
        return False
    for deployment in deployments:
        if deployment.mode is not mode:
            continue
        try:
            snapshot = await store.get_deployment(deployment.id)
        except ExecutionStoreError:
            continue
        if any(fill.id == fill_id for fill in snapshot.fills):
            return True
    return False


class ExperientialEvidenceError(ValueError):
    """Signal that evidence storage cannot prove a training pointer."""
