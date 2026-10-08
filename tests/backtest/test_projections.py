"""Exercise bounded HTTP reads and PostgreSQL publication integrity end to end."""

from __future__ import annotations

import asyncio
import json
import os
from typing import TYPE_CHECKING, NoReturn

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlalchemy import update

from tests.api.test_backtests import InMemoryBacktestResultReader, _result
from thytrader.api.app import create_app
from thytrader.backtest.cost_attribution import BacktestCostAttribution, compute_cost_attribution
from thytrader.backtest.kernel import simulate_backtest_with_diagnostics
from thytrader.backtest.models import BacktestResult, backtest_result_fingerprint
from thytrader.backtest.projections import BacktestProjection
from thytrader.config import Settings
from thytrader.evaluation.signal_evaluator import evaluate_signal_trace
from thytrader.persistence.backtest_results import BacktestResultIntegrityError
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.schema import published_backtest_results, published_research_run_specs

from .test_kernel import _candles, _run, _strategy
from .test_persistence import _cleanup_seeded_sources, _result_store, _seed_sources

if TYPE_CHECKING:
    from pathlib import Path


class _ProjectionStore(InMemoryBacktestResultReader):
    """Expose bounded evidence while rejecting any accidental full-ledger read."""

    async def load(self, result_fingerprint: str) -> NoReturn:
        """Make materializing a full result an observable test failure."""
        del result_fingerprint
        raise AssertionError("bounded reads must not load a full ledger")

    async def load_projections(
        self, result_fingerprints: tuple[str, ...]
    ) -> tuple[BacktestProjection, ...]:
        """Return small projections of the supplied immutable test publications."""
        return tuple(
            BacktestProjection(
                result_fingerprint=fingerprint,
                run_fingerprint=result.run_fingerprint,
                strategy_fingerprint=result.strategy_fingerprint,
                dataset_fingerprint=result.dataset_fingerprint,
                summary=result.summary,
            )
            for fingerprint in result_fingerprints
            for result in (self._results[fingerprint],)
        )


def test_summary_and_export_never_load_full_ledgers(tmp_path: Path) -> None:
    """Real HTTP requests use the bounded capability for both detail and export."""
    result = _result()
    store = _ProjectionStore((result,))
    app = create_app(Settings(), backtest_result_store=store)
    with TestClient(app) as client:
        fingerprint = backtest_result_fingerprint(result)
        response = client.get(f"/api/v1/backtests/{fingerprint}")
        assert response.status_code == 200
        assert response.json()["verification_scope"] == "publication"
        assert response.json()["cost_attribution"] is None
        assert "equity_curve" not in response.json()
        exported = client.get("/api/v1/backtests/export?limit=1")
        assert exported.status_code == 200
        assert exported.json()["returned"] == 1
        assert exported.json()["has_more"] is False
        assert exported.json()["entries"][0]["summary"] == response.json()["summary"]
        assert exported.json()["entries"][0]["cost_attribution"] is None
        assert client.get("/api/v1/backtests/export?limit=101").status_code == 422
        assert client.get("/api/v1/backtests/export?cursor=invalid").status_code == 400
    del tmp_path


def test_postgres_projection_checks_bytes_sources_and_optional_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use a real database to reject tampering without loading a dataset or ledger."""
    database_url = os.environ.get("THYTRADER_INTEGRATION_DATABASE_URL")
    if database_url is None:
        pytest.skip("THYTRADER_INTEGRATION_DATABASE_URL is not configured")
    asyncio.run(_assert_postgres_projection(database_url, monkeypatch))


async def _assert_postgres_projection(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Publish once, then inspect and corrupt only records in the isolated test database."""
    strategy = _strategy()
    specification = _run(strategy)
    result, diagnostics = simulate_backtest_with_diagnostics(specification, strategy, _candles())
    engine = create_engine(SecretStr(database_url))
    fingerprint = backtest_result_fingerprint(result)
    try:
        await _seed_sources(engine, result, strategy, specification)
        store = _result_store(engine, specification)
        trace = evaluate_signal_trace(specification, strategy, _candles())
        await store.publish(result, trace=trace, diagnostics=diagnostics)

        async def forbidden_load(result_fingerprint: str) -> BacktestResult:
            """Prevent this test from succeeding through the expensive fallback."""
            del result_fingerprint
            raise AssertionError("must not materialize full evidence")

        monkeypatch.setattr(store, "load", forbidden_load)
        projection = (await store.load_projections((fingerprint,)))[0]
        assert projection.summary == result.summary
        assert projection.costs == specification.costs
        assert projection.diagnostics == diagnostics
        assert projection.metrics is not None
        assert projection.metrics.result_fingerprint == fingerprint
        attribution = compute_cost_attribution(result)
        assert projection.cost_attribution == attribution
        assert projection.window is not None
        assert projection.window.warmup_bars == specification.warmup.bars
        async with engine.begin() as connection:
            await connection.execute(
                update(published_backtest_results)
                .where(published_backtest_results.c.result_fingerprint == fingerprint)
                .values(metrics_json=None, cost_attribution_json=None)
            )
        legacy = (await store.load_projections((fingerprint,)))[0]
        assert legacy.metrics is None and legacy.warnings
        assert legacy.cost_attribution is None
        assert any("Fee attribution" in warning for warning in legacy.warnings)
        # A verified republish fills missing metadata without changing result identity.
        restored_store = _result_store(engine, specification)
        await restored_store.publish(result, trace=trace)
        restored = (await store.load_projections((fingerprint,)))[0]
        assert restored.cost_attribution == attribution
        assert restored.warnings == ()
        for placeholder in (None, "sha256:" + "0" * 64):
            unsigned = attribution.model_dump(mode="python", exclude={"attribution_fingerprint"})
            if placeholder is not None:
                unsigned["attribution_fingerprint"] = placeholder
            async with engine.begin() as connection:
                await connection.execute(
                    update(published_backtest_results)
                    .where(published_backtest_results.c.result_fingerprint == fingerprint)
                    .values(cost_attribution_json=json.dumps(unsigned))
                )
            with pytest.raises(BacktestResultIntegrityError):
                await store.load_projections((fingerprint,))
        corrupted = attribution.model_dump(mode="python")
        corrupted["entry_fees"] = "999"
        async with engine.begin() as connection:
            await connection.execute(
                update(published_backtest_results)
                .where(published_backtest_results.c.result_fingerprint == fingerprint)
                .values(cost_attribution_json=json.dumps(corrupted))
            )
        with pytest.raises(BacktestResultIntegrityError):
            await store.load_projections((fingerprint,))
        wrong_source = attribution.model_dump(mode="python", exclude={"attribution_fingerprint"})
        wrong_source["run_fingerprint"] = "sha256:" + "9" * 64
        wrong_attribution = BacktestCostAttribution.model_validate(wrong_source)
        async with engine.begin() as connection:
            await connection.execute(
                update(published_backtest_results)
                .where(published_backtest_results.c.result_fingerprint == fingerprint)
                .values(cost_attribution_json=wrong_attribution.model_dump_json())
            )
        with pytest.raises(BacktestResultIntegrityError, match="source identity"):
            await store.load_projections((fingerprint,))
        async with engine.begin() as connection:
            await connection.execute(
                update(published_backtest_results)
                .where(published_backtest_results.c.result_fingerprint == fingerprint)
                .values(cost_attribution_json=attribution.model_dump_json())
            )
        async with engine.begin() as connection:
            await connection.execute(
                update(published_research_run_specs)
                .where(published_research_run_specs.c.run_fingerprint == result.run_fingerprint)
                .values(
                    canonical_specification=published_research_run_specs.c.canonical_specification
                    + " "
                )
            )
        with pytest.raises(BacktestResultIntegrityError, match="digest"):
            await store.load_projections((fingerprint,))
    finally:
        await _cleanup_seeded_sources(engine, result)
        await dispose(engine)
