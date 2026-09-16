"""Operator strategies/runtime reports expose redacted per-product books."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from thytrader.config import Settings
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentStatus,
    Position,
    PositionSide,
    RuntimePhase,
)
from thytrader.market_data.worker_state import DisabledMarketDataWorkerStateStore
from thytrader.operator.service import OperatorDiagnostics
from thytrader.persistence.audit_events import InMemoryAuditEventStore
from thytrader.persistence.backtest_results import DisabledBacktestResultStore
from thytrader.persistence.portfolio_history import InMemoryPortfolioHistoryStore
from thytrader.portfolio.demo import DemoExchangeAccount
from thytrader.portfolio.service import PortfolioService
from thytrader.strategies.authoring import DisabledStrategyDraftStore, create_reference_draft
from thytrader.strategies.models import Instrument, StrategyDefinition, StrategyStatus
from thytrader.strategies.publication import StrategyCatalogEntry


class _Catalog:
    """Publication catalog that can list one two-product document."""

    def __init__(self, entry: StrategyCatalogEntry) -> None:
        """Retain one catalog row."""
        self._entry = entry

    async def list_published(self, *, include_archived: bool) -> tuple[StrategyCatalogEntry, ...]:
        """Return the retained publication."""
        del include_archived
        return (self._entry,)

    async def archive(self, strategy_fingerprint_value: str) -> StrategyCatalogEntry:
        """Unused archive path."""
        del strategy_fingerprint_value
        return self._entry


def _now() -> datetime:
    """Return a UTC instant used by fixtures."""
    return datetime(2026, 9, 16, tzinfo=UTC)


def _two_product_definition() -> StrategyDefinition:
    """Return a published BTC primary with ETH extra coverage."""
    draft = create_reference_draft(now=_now())
    extra = Instrument(product_id="ETH-USD", base_currency="ETH", quote_currency="USD")
    limits = draft.portfolio_limits.model_copy(update={"max_concurrent_positions": 2})
    return StrategyDefinition.model_validate(
        {
            **draft.model_dump(mode="python"),
            "status": StrategyStatus.PUBLISHED.value,
            "additional_instruments": [extra.model_dump(mode="python")],
            "portfolio_limits": limits.model_dump(mode="python"),
        }
    )


def test_operator_books_omit_quantities_and_label_each_product() -> None:
    """Primary flat and secondary open stay distinct without leaking size."""
    definition = _two_product_definition()
    fingerprint = "sha256:" + ("c" * 64)
    catalog = _Catalog(
        StrategyCatalogEntry(
            strategy_fingerprint=fingerprint,
            definition=definition,
            archived_at=None,
        )
    )
    store = InMemoryExecutionStore()
    deployment = Deployment(
        id=uuid4(),
        strategy_fingerprint=fingerprint,
        strategy_id=definition.strategy_id,
        product_id="BTC-USD",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("10000"),
        phase=RuntimePhase.OPEN,
        created_at=_now(),
        updated_at=_now(),
        timeframe="1h",
    )
    position = Position(
        deployment_id=deployment.id,
        quantity=Decimal("0.5"),
        entry_price=Decimal("3000"),
        stop_price=Decimal("3200"),
        target_price=Decimal("2700"),
        entered_bar=_now(),
        updated_at=_now(),
        side=PositionSide.SHORT,
        product_id="ETH-USD",
    )
    asyncio.run(store.create_deployment(deployment))
    asyncio.run(store.save_position(position, deployment_id=deployment.id))
    diagnostics = OperatorDiagnostics(
        settings=Settings(_env_file=None),
        portfolio=PortfolioService(DemoExchangeAccount(), demo=True),
        market_data_state=DisabledMarketDataWorkerStateStore(),
        history=InMemoryPortfolioHistoryStore(),
        publications=catalog,
        drafts=DisabledStrategyDraftStore(),
        backtests=DisabledBacktestResultStore(),
        execution=store,
        audit=InMemoryAuditEventStore(),
    )
    report = asyncio.run(diagnostics.strategies())
    row = report.payload.deployments[0]
    by_product = {item.product_id: item for item in row.books}
    assert set(by_product) == {"BTC-USD", "ETH-USD"}
    assert by_product["BTC-USD"].phase == "flat"
    assert by_product["BTC-USD"].side is None
    assert by_product["BTC-USD"].protection_status == "flat"
    assert by_product["ETH-USD"].phase == "open"
    assert by_product["ETH-USD"].side == "short"
    assert by_product["ETH-USD"].protection_status == "unprotected"
    dumped = row.model_dump()
    assert "0.5" not in str(dumped)
    assert "3000" not in str(dumped)
