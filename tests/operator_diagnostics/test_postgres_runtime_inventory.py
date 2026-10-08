"""Independent PostgreSQL replay of missing-product reporting and false-recovery review."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from tests.alerts.test_evidence_recovery import _alert
from tests.alerts.test_supervision import _NOW, _deployment, _no_candles, _position
from tests.fleet_control.test_postgres_safety import (
    _URL,
    _engine,
    anyio_backend,
    private_schema,
)
from thytrader.alerts.models import AlertCode
from thytrader.alerts.service import AlertService
from thytrader.alerts.supervision import gather_safety_findings
from thytrader.alerts.supervision_inputs import AlertThresholds
from thytrader.memory.notify import DisabledNotificationSender
from thytrader.persistence.postgres_alerts import PostgresAlertStore
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.postgres_strategies import PostgresStrategyStore
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import create_strategy_from_definition
from thytrader.strategies.models import StrategyDefinition
from thytrader.trading.fill_ledger import unprojected_inventory_products, unsettled_fill_evidence
from thytrader.trading.ledger import ledger_from_snapshot
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentStatus,
    InstrumentRuntime,
    RuntimePhase,
)
from thytrader.trading.protection import (
    book_protection_evidence,
    missing_occupied_inventory_products,
)
from thytrader.trading.protection_models import ProtectionStatus

# Explicit fixture re-exports keep discovery local to this module.
__all__ = ["anyio_backend", "private_schema"]

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(_URL is None, reason="Private test PostgreSQL URL required."),
]


@pytest.mark.parametrize("status", list(DeploymentStatus))
@pytest.mark.parametrize("phase", [RuntimePhase.OPEN, RuntimePhase.PENDING_EXIT])
@pytest.mark.parametrize("mode", list(DeploymentMode))
async def test_reloaded_missing_product_preserves_alerts_until_verified_flat(
    private_schema: str, status: DeploymentStatus, phase: RuntimePhase, mode: DeploymentMode
) -> None:
    """Real migrated rows survive engine restart without borrowing a sibling's inventory."""
    seed_engine = _engine(private_schema)
    try:
        execution = PostgresExecutionStore(seed_engine)
        template = create_template_strategy(product_id="BTC-USD", timeframe="1h")
        definition = StrategyDefinition.model_validate(
            {
                **template.model_dump(mode="json"),
                "additional_instruments": [
                    {"product_id": "ETH-USD", "base_currency": "ETH", "quote_currency": "USD"}
                ],
            }
        )
        strategies = PostgresStrategyStore(seed_engine)
        root = await create_strategy_from_definition(strategies, definition)
        bound = await strategies.snapshot(root.strategy_id)
        book = await execution.create_deployment(
            replace(
                _deployment(mode=mode, phase=RuntimePhase.OPEN, status=status),
                strategy_id=root.strategy_id,
                strategy_fingerprint=bound.strategy_fingerprint,
                paper_starting_cash=Decimal("10000") if mode is DeploymentMode.PAPER else None,
                paper_maker_fee_rate=Decimal("0.001") if mode is DeploymentMode.PAPER else None,
                paper_taker_fee_rate=Decimal("0.002") if mode is DeploymentMode.PAPER else None,
            )
        )
        await execution.save_position(_position(book), deployment_id=book.id, product_id="BTC-USD")
        for product, state in (("BTC-USD", RuntimePhase.OPEN), ("ETH-USD", phase)):
            await execution.save_instrument_runtime(
                InstrumentRuntime(product, state), deployment_id=book.id
            )
        alerts = PostgresAlertStore(seed_engine)
        prior = tuple(
            [
                (
                    await alerts.record(
                        replace(_alert(book, code, f"{book.id}:ETH-USD"), product_id="ETH-USD"),
                        now=_NOW,
                    )
                ).alert
                for code in (AlertCode.STOP_UNCOVERED, AlertCode.STOP_COVERAGE_UNKNOWN)
            ]
        )
    finally:
        await seed_engine.dispose()

    restarted_engine = _engine(private_schema)
    try:
        execution = PostgresExecutionStore(restarted_engine)
        alerts = PostgresAlertStore(restarted_engine)
        snapshot = await execution.get_accounting_snapshot(book.id)
        assert len(snapshot.positions) == 1 and len(snapshot.instrument_runtimes) == 2
        assert not unprojected_inventory_products(snapshot)
        assert not unsettled_fill_evidence(snapshot)
        assert missing_occupied_inventory_products(snapshot) == ("ETH-USD",)
        evidence = book_protection_evidence(snapshot, product_id="ETH-USD", position=None, now=_NOW)
        assert evidence.status is ProtectionStatus.UNKNOWN
        assert evidence.required_quantity is evidence.covered_quantity is None
        assert evidence.uncovered_quantity is None
        assert "runtime_position_unresolved" in evidence.reasons
        ledger = ledger_from_snapshot(snapshot, marks={"BTC-USD": Decimal("101")})
        assert not ledger.accounting_complete and ledger.equity is None
        assert ledger.books[0].mark_complete
        service = AlertService(alerts, DisabledNotificationSender(), thresholds=AlertThresholds())
        for verified_flat in (False, True):
            if verified_flat:
                # The positive control supplies explicit new flat runtime evidence, not
                # an inferred quantity, a canceled order, or a report-induced repair.
                await execution.save_instrument_runtime(
                    InstrumentRuntime("ETH-USD", RuntimePhase.FLAT), deployment_id=book.id
                )
            now = _NOW + timedelta(seconds=2 if verified_flat else 1)
            findings = await gather_safety_findings(
                deployments=(book,),
                snapshots=execution,
                closed_candles=_no_candles,
                now=now,
                thresholds=AlertThresholds(),
                worker_interval_seconds=30,
                prior_alerts=prior,
            )
            await service.apply(
                findings.findings, evaluated=findings.evaluated, now=now, dispatch=False
            )
            open_ids = {row.id for row in await alerts.list_open_alerts()}
            assert all((row.id not in open_ids) is verified_flat for row in prior)
            loaded = await execution.get_accounting_snapshot(book.id)
            assert loaded.deployment.status is status
            assert len(loaded.positions) == 1
            assert loaded.positions[0] == snapshot.positions[0]
            assert ledger_from_snapshot(loaded).accounting_complete is verified_flat
    finally:
        await restarted_engine.dispose()
