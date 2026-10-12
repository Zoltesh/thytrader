"""Synthetic public performance regressions for preview freshness and provenance."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from tests.http_fakes import matching_ready_payload, urlopen_ready_then
from tests.operator_diagnostics.test_deployment_performance import (
    _CloseProvider,
    _diagnostics,
    _fill,
    _now,
    _order,
    _round_trip_store,
)
from thytrader.api.app import create_app
from thytrader.api.routes.operator import get_operator_diagnostics
from thytrader.config import Settings
from thytrader.market_data.quality import analyze_candles
from thytrader.market_data.service import MarketDataService
from thytrader.operator.cli import main
from thytrader.operator.models import ReportStatus
from thytrader.operator.runtime_models import PerformanceReport
from thytrader.ops_contract import expected_ops_contract
from thytrader.trading.models import OrderKind, OrderSide, Position, RuntimePhase

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from thytrader.market_data.models import CandleInterval, MarketDataPreview
    from thytrader.operator.service import OperatorDiagnostics
    from thytrader.trading.memory import InMemoryExecutionStore


class ValuationProvider(_CloseProvider):
    """Serve independently controlled preview states, without a venue or database."""

    state = "fresh"

    async def get_recent_preview(
        self, product_id: str, interval: CandleInterval, now: datetime
    ) -> MarketDataPreview:
        """Retain a recent candle even when the provider explicitly rejects freshness."""
        preview = await super().get_recent_preview(product_id, interval, now)
        preview = replace(preview, product=replace(preview.product, product_id=product_id))
        if self.state == "failed":
            raise RuntimeError("synthetic provider failure")
        if self.state == "empty":
            return replace(preview, quality=analyze_candles((), interval, now))
        if self.state == "stale":
            return replace(preview, quality=replace(preview.quality, is_stale=True))
        if self.state == "old":
            candle = replace(
                preview.quality.candles[0],
                starts_at=preview.quality.candles[0].starts_at - interval.duration * 3,
            )
            return replace(preview, quality=analyze_candles((candle,), interval, now))
        return preview


async def open_after_round_trip() -> tuple[InMemoryExecutionStore, UUID]:
    """Keep a known closed trade and paid fees alongside one open synthetic unit."""
    store, deployment_id = await _round_trip_store(fee=Decimal("0.1"))
    snapshot = await store.get_deployment(deployment_id)
    await store.save_deployment(
        replace(snapshot.deployment, cash=Decimal("9909.7"), phase=RuntimePhase.OPEN)
    )
    buy = _order(
        deployment_id=deployment_id,
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        price=Decimal("100"),
        client_order_id="second-buy",
    )
    await store.save_order(buy)
    await store.save_fill(
        _fill(
            deployment_id=deployment_id,
            order_id=buy.id,
            price=Decimal("100"),
            fee=Decimal("0.1"),
            hour=3,
        )
    )
    await store.save_position(
        Position(
            deployment_id=deployment_id,
            quantity=Decimal("1"),
            entry_price=Decimal("100"),
            stop_price=Decimal("90"),
            target_price=Decimal("120"),
            entered_bar=_now() + timedelta(hours=3),
            updated_at=_now(),
        ),
        deployment_id=deployment_id,
    )
    return store, deployment_id


def test_stale_preview_fails_closed_and_recovers_through_public_service() -> None:
    """Provider stale flags and old candles cannot value current inventory."""

    async def scenario() -> None:
        """Reobserve every provider state rather than reusing an earlier accepted mark."""
        store, deployment_id = await open_after_round_trip()
        provider = ValuationProvider(Decimal("105"))
        diagnostics = _diagnostics(execution=store, market_data=MarketDataService(provider))
        for state in ("fresh", "stale", "old", "failed", "empty", "fresh"):
            provider.state = state
            report = await diagnostics.performance(deployment_id=deployment_id)
            assert report.payload.trade_count == 1
            if state == "fresh":
                assert report.payload.total_net_pnl == "14.7"
                assert report.payload.mark_complete is True
            else:
                assert report.payload.total_net_pnl is None, state
                assert report.payload.total_return_fraction is None
                assert report.payload.maximum_drawdown_fraction is None
                assert report.payload.marked_exposure is None
                assert report.payload.mark_complete is False
                assert report.overall_status is ReportStatus.DEGRADED
                assert report.components[0].reason_code == "MISSING_MARK"

    asyncio.run(scenario())


@pytest.mark.parametrize("state", ["fresh", "stale", "old", "failed", "empty"])
def test_receipt_survives_http_and_validated_cli(
    state: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The same synthetic receipt and known fill facts cross all operator boundaries."""
    store, deployment_id = asyncio.run(open_after_round_trip())
    provider = ValuationProvider(Decimal("105"))
    provider.state = state
    diagnostics = _diagnostics(execution=store, market_data=MarketDataService(provider))

    def override() -> OperatorDiagnostics:
        """Inject in-memory services into the shipped HTTP route."""
        return diagnostics

    app = create_app(Settings(_env_file=None))
    app.dependency_overrides[get_operator_diagnostics] = override
    with TestClient(app) as client:
        response = client.get(f"/api/v1/operator/performance?deployment_id={deployment_id}")
    assert response.status_code == 200
    report = PerformanceReport.model_validate_json(response.content)
    payload = report.model_dump(mode="json")["payload"]
    assert payload["realized_net_pnl"] == "9.8"
    assert payload["total_fees"] == "0.3"
    receipt = payload["books"][0]["valuation"]
    expected = {"old": "stale", "failed": "unavailable"}.get(state, state)
    assert receipt["status"] == expected
    assert receipt["source"] == "market_data_preview"
    assert receipt["used"] is (state == "fresh")
    if state in {"fresh", "stale", "old"}:
        assert receipt["price"] == "105"
        assert receipt["candle_closed_at"] is not None
        assert receipt["observed_at"] is not None
    else:
        assert receipt["price"] is None
        assert receipt["candle_closed_at"] is None
        assert (receipt["observed_at"] is not None) is (state == "empty")
    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(matching_ready_payload(), response.json()),
        ),
        pytest.raises(SystemExit) as exited,
    ):
        main(["performance", "--deployment-id", str(deployment_id)])
    assert exited.value.code == (0 if state == "fresh" else 1)
    validated = PerformanceReport.model_validate_json(capsys.readouterr().out)
    assert validated == report


def test_performance_cli_text_labels_valuation(capsys: pytest.CaptureFixture[str]) -> None:
    """Text output names rejected candidates and known closed-trade economics."""
    store, deployment_id = asyncio.run(open_after_round_trip())
    provider = ValuationProvider(Decimal("105"))
    provider.state = "stale"
    report = asyncio.run(
        _diagnostics(execution=store, market_data=MarketDataService(provider)).performance(
            deployment_id=deployment_id
        )
    )
    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(
                matching_ready_payload(), report.model_dump(mode="json")
            ),
        ),
        pytest.raises(SystemExit) as exited,
    ):
        main(["performance", "--deployment-id", str(deployment_id), "--format", "text"])
    assert exited.value.code == 1
    text = capsys.readouterr().out
    assert "Performance valuation: freshly observed preview" in text
    assert "BTC-USD: stale; not used; STALE_MARK" in text
    assert "candidate_price=105" in text
    assert "candle_closed_at=" in text
    assert "observed_at=" in text
    assert "source=market_data_preview" in text
    assert "recorded_realized_net_pnl=9.8" in text
    assert "paid_fees=0.3" in text


def test_ops_contract_advertises_performance_valuation_receipts() -> None:
    """Agents can reject stale images lacking truthful valuation provenance."""
    capabilities = expected_ops_contract()["runtime_observability"]
    assert isinstance(capabilities, list)
    assert "performance_valuation_receipts" in capabilities


@pytest.mark.anyio
async def test_flat_trade_history_needs_no_preview_or_fabricated_receipt() -> None:
    """Flat books retain round trips without claiming an unused fresh price."""
    store, deployment_id = await _round_trip_store(fee=Decimal("0.1"))
    provider = ValuationProvider(Decimal("105"))
    provider.state = "failed"
    report = await _diagnostics(
        execution=store, market_data=MarketDataService(provider)
    ).performance(deployment_id=deployment_id)
    assert report.overall_status is ReportStatus.HEALTHY
    assert report.payload.total_net_pnl == "9.8"
    assert report.payload.books
    book = report.payload.books[0].model_dump(mode="json")
    assert book["product_id"] == "BTC-USD"
    assert book["trade_count"] == 1
    assert book["total_net_pnl"] == "9.8"
    assert book["valuation"] == {
        "status": "not_required",
        "source": "market_data_preview",
        "used": False,
        "price": None,
        "candle_closed_at": None,
        "observed_at": None,
        "reason_code": "NO_OPEN_INVENTORY",
    }
