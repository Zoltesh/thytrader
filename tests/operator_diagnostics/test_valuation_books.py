"""Independent multibook and timestamp-boundary performance evidence."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from tests.http_fakes import matching_ready_payload, urlopen_ready_then
from tests.operator_diagnostics.test_deployment_performance import (
    _diagnostics,
    _fill,
    _now,
    _order,
    _round_trip_store,
)
from tests.operator_diagnostics.test_performance_valuation import (
    ValuationProvider,
    open_after_round_trip,
)
from thytrader.api.app import create_app
from thytrader.api.routes.operator import get_operator_diagnostics
from thytrader.config import Settings
from thytrader.market_data.quality import analyze_candles
from thytrader.market_data.service import MarketDataService
from thytrader.operator.cli import main
from thytrader.operator.runtime_models import PerformanceReport
from thytrader.trading.ledger import ledger_from_snapshot
from thytrader.trading.models import OrderKind, OrderSide, Position, RuntimePhase

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.market_data.models import CandleInterval, MarketDataPreview, MarketProduct
    from thytrader.operator.service import OperatorDiagnostics
    from thytrader.trading.memory import InMemoryExecutionStore
    from thytrader.trading.models import DeploymentSnapshot


class ProductPreviews:
    """Independently control sibling previews and record which books required a read."""

    def __init__(self) -> None:
        """Use distinct round synthetic prices; initially reject only ETH."""
        self.eth_stale = True
        self.calls: list[str] = []

    async def get_recent_preview(
        self, product_id: str, interval: CandleInterval, now: datetime
    ) -> MarketDataPreview:
        """Return product-specific evidence through the normal quality boundary."""
        self.calls.append(product_id)
        provider = ValuationProvider(Decimal("205") if product_id == "ETH-USD" else Decimal("105"))
        provider.state = "old" if product_id == "ETH-USD" and self.eth_stale else "fresh"
        return await provider.get_recent_preview(product_id, interval, now)

    async def list_products(self) -> tuple[MarketProduct, ...]:
        """No catalog reads are needed for valuation."""
        return ()


async def add_eth(store: InMemoryExecutionStore, deployment_id: UUID) -> None:
    """Add one separate ETH unit with its own fill economics."""
    snapshot = await store.get_deployment(deployment_id)
    await store.save_deployment(
        replace(
            snapshot.deployment,
            cash=snapshot.deployment.cash - Decimal("200.2"),
            phase=RuntimePhase.OPEN,
        )
    )
    order = replace(
        _order(
            deployment_id=deployment_id,
            side=OrderSide.BUY,
            kind=OrderKind.POST_ONLY_LIMIT,
            price=Decimal("200"),
            client_order_id="eth-entry",
        ),
        product_id="ETH-USD",
    )
    await store.save_order(order)
    await store.save_fill(
        _fill(
            deployment_id=deployment_id,
            order_id=order.id,
            price=Decimal("200"),
            fee=Decimal("0.2"),
            hour=4,
        )
    )
    await store.save_position(
        Position(
            deployment_id=deployment_id,
            product_id="ETH-USD",
            quantity=Decimal("1"),
            entry_price=Decimal("200"),
            stop_price=Decimal("180"),
            target_price=Decimal("240"),
            entered_bar=_now() + timedelta(hours=4),
            updated_at=_now(),
        ),
        deployment_id=deployment_id,
    )


def http_and_cli(
    diagnostics: OperatorDiagnostics, deployment_id: UUID, capsys: pytest.CaptureFixture[str]
) -> PerformanceReport:
    """Exercise real HTTP serialization followed by the validating shipped CLI."""

    def override() -> OperatorDiagnostics:
        """Keep all reads on the synthetic services."""
        return diagnostics

    app = create_app(Settings(_env_file=None))
    app.dependency_overrides[get_operator_diagnostics] = override
    with TestClient(app) as client:
        response = client.get(f"/api/v1/operator/performance?deployment_id={deployment_id}")
    assert response.status_code == 200
    report = PerformanceReport.model_validate_json(response.content)
    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(matching_ready_payload(), response.json()),
        ),
        pytest.raises(SystemExit) as exited,
    ):
        main(["performance", "--deployment-id", str(deployment_id)])
    assert exited.value.code == (0 if report.payload.mark_complete else 1)
    assert PerformanceReport.model_validate_json(capsys.readouterr().out) == report
    return report


@pytest.mark.parametrize("primary_open", [False, True])
def test_multibook_receipts_preserve_trade_only_books_and_recover(
    *,
    primary_open: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One stale sibling blocks aggregate valuation, never another book's receipt."""
    store, deployment_id = asyncio.run(
        open_after_round_trip() if primary_open else _round_trip_store(fee=Decimal("0.1"))
    )
    asyncio.run(add_eth(store, deployment_id))
    snapshot = asyncio.run(store.get_deployment(deployment_id))
    # Existing open-book slices use shared deployment cash; this fix must not change it.
    existing = ledger_from_snapshot(snapshot, marks={"BTC-USD": Decimal("105")})
    expected_btc = (
        format(existing.books[0].total_net_pnl, "f")
        if primary_open and existing.books[0].total_net_pnl is not None
        else None
    )
    provider = ProductPreviews()
    diagnostics = _diagnostics(execution=store, market_data=MarketDataService(provider))
    for stale in (True, False):
        provider.eth_stale = stale
        report = http_and_cli(diagnostics, deployment_id, capsys)
        books = {book.product_id: book for book in report.payload.books}
        assert set(books) == {"BTC-USD", "ETH-USD"}
        btc, eth = books["BTC-USD"], books["ETH-USD"]
        assert report.payload.trade_count == 1
        assert report.payload.realized_net_pnl == "9.8"
        assert report.payload.total_fees == ("0.5" if primary_open else "0.4")
        assert btc.trade_count == 1
        assert btc.realized_net_pnl == "9.8"
        assert btc.total_fees == ("0.3" if primary_open else "0.2")
        assert btc.valuation.status == ("fresh" if primary_open else "not_required")
        assert btc.valuation.used is primary_open
        assert btc.valuation.price == ("105" if primary_open else None)
        assert btc.total_net_pnl == expected_btc
        assert eth.valuation.status == ("stale" if stale else "fresh")
        assert eth.valuation.used is not stale
        assert eth.valuation.price == "205"
        assert eth.valuation.candle_closed_at is not None
        assert eth.valuation.observed_at is not None
        assert report.payload.mark_complete is not stale
        assert (report.payload.total_net_pnl is None) is stale
        assert (report.payload.marked_exposure is None) is stale
        if stale:
            assert eth.total_net_pnl is None
            assert any(
                "ETH-USD: STALE_MARK" in warning for warning in report.partial_result_warnings
            )
    assert provider.calls == (["BTC-USD", "ETH-USD"] * 2 if primary_open else ["ETH-USD"] * 2)


@pytest.mark.anyio
async def test_trade_only_receipt_does_not_certify_incomplete_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Known closed trades cannot certify flatness when the snapshot scope is incomplete."""
    store, deployment_id = await _round_trip_store(fee=Decimal("0.1"))
    snapshot = replace(await store.get_deployment(deployment_id), accounting_complete=False)

    async def load(requested_id: UUID) -> DeploymentSnapshot:
        """Return a successful but explicitly incomplete synthetic snapshot."""
        assert requested_id == deployment_id
        return snapshot

    monkeypatch.setattr(store, "get_deployment", load)
    report = await _diagnostics(execution=store).performance(deployment_id=deployment_id)
    assert report.payload.total_net_pnl is None
    assert report.payload.trade_count == 1
    assert report.payload.realized_net_pnl == "9.8"
    book = report.payload.books[0]
    assert not book.mark_complete
    assert book.total_net_pnl is None
    assert not book.valuation.used
    assert book.valuation.reason_code == "ACCOUNTING_UNRESOLVED"


class BoundaryPreview(ValuationProvider):
    """Return precise fixed-time evidence on either side of the existing stale boundary."""

    def __init__(self, extra: timedelta) -> None:
        """Vary observation time without changing the candle-close timestamp."""
        super().__init__(Decimal("105"))
        self.extra = extra

    async def get_recent_preview(
        self, product_id: str, interval: CandleInterval, now: datetime
    ) -> MarketDataPreview:
        """Analyze at exactly two intervals old, plus the test's microsecond offset."""
        del now
        closed_at = datetime(2026, 1, 2, tzinfo=UTC)
        preview = await super().get_recent_preview(product_id, interval, closed_at)
        as_of = closed_at + interval.duration * 2 + self.extra
        return replace(
            preview, as_of=as_of, quality=analyze_candles(preview.quality.candles, interval, as_of)
        )


@pytest.mark.anyio
@pytest.mark.parametrize("extra", [timedelta(0), timedelta(microseconds=1)])
async def test_exact_two_interval_age_rule_and_receipt_times(extra: timedelta) -> None:
    """Strictly greater than two intervals is stale; report time never rewrites provenance."""
    store, deployment_id = await open_after_round_trip()
    report = await _diagnostics(
        execution=store, market_data=MarketDataService(BoundaryPreview(extra))
    ).performance(deployment_id=deployment_id)
    receipt = report.payload.books[0].valuation
    assert receipt.candle_closed_at == datetime(2026, 1, 2, tzinfo=UTC)
    assert receipt.observed_at == datetime(2026, 1, 2, 2, tzinfo=UTC) + extra
    assert receipt.used is (extra == timedelta(0))
    assert receipt.status == ("fresh" if extra == timedelta(0) else "stale")
