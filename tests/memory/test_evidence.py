"""Fail-closed local evidence lookup never calls Coinbase."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from thytrader.execution.store import DisabledExecutionStore
from thytrader.market_data.datasets import DatasetStore
from thytrader.memory.evidence import (
    ExperientialEvidenceError,
    LocalEvidenceResolver,
    StaticEvidenceResolver,
)
from thytrader.memory.models import EvidenceKind
from thytrader.persistence.backtest_results import DisabledBacktestResultStore


def test_static_resolver_allowlists_pairs() -> None:
    """Only registered (kind, id) pairs count as local evidence."""
    resolver = StaticEvidenceResolver(frozenset({(EvidenceKind.BACKTEST, "bt-1")}))
    assert asyncio.run(resolver.exists(EvidenceKind.BACKTEST, "bt-1")) is True
    assert asyncio.run(resolver.exists(EvidenceKind.BACKTEST, "bt-2")) is False
    assert asyncio.run(resolver.exists(EvidenceKind.PAPER_FILL, "bt-1")) is False


def test_local_resolver_unavailable_backtests_fail_closed(tmp_path: Path) -> None:
    """Disabled backtest storage is an error, not an empty catalog."""
    resolver = LocalEvidenceResolver(
        backtests=DisabledBacktestResultStore(),
        datasets=DatasetStore(tmp_path / "missing-datasets"),
        execution=DisabledExecutionStore(),
    )
    with pytest.raises(ExperientialEvidenceError, match="unavailable"):
        asyncio.run(resolver.exists(EvidenceKind.BACKTEST, "sha256:" + ("a" * 64)))
    with pytest.raises(ExperientialEvidenceError, match="unavailable"):
        asyncio.run(resolver.exists(EvidenceKind.RESEARCH, "run-fp"))


def test_local_resolver_missing_dataset_and_deployment_are_absent(tmp_path: Path) -> None:
    """Unknown local ids are absent; they do not interpolate candles."""
    resolver = LocalEvidenceResolver(
        backtests=DisabledBacktestResultStore(),
        datasets=DatasetStore(tmp_path / "missing-datasets"),
        execution=DisabledExecutionStore(),
    )
    assert asyncio.run(resolver.exists(EvidenceKind.MARKET_DATA, "missing")) is False
    assert asyncio.run(resolver.exists(EvidenceKind.DEPLOYMENT, str(uuid4()))) is False
    assert asyncio.run(resolver.exists(EvidenceKind.NONE, "x")) is False
    assert asyncio.run(resolver.exists(EvidenceKind.LIVE_FILL, str(uuid4()))) is False
