"""Product-isolated recorded statistics through HTTP and the validating operator CLI."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from tests.http_fakes import matching_ready_payload, urlopen_ready_then
from tests.operator_diagnostics.test_deployment_performance import (
    _deployment,
    _diagnostics,
    _fill,
    _now,
    _order,
)
from tests.operator_diagnostics.test_performance_valuation import ValuationProvider
from tests.operator_diagnostics.test_valuation_books import http_and_cli
from thytrader.market_data.service import MarketDataService
from thytrader.operator.cli import main
from thytrader.trading.ledger import ledger_from_snapshot
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import OrderKind, OrderSide, Position, RuntimePhase

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from thytrader.market_data.models import CandleInterval, MarketDataPreview, MarketProduct
    from thytrader.operator.runtime_models import PerformanceReport


class RecordedPreviews:
    """Control all open marks while recording that closed siblings never cause reads."""

    def __init__(self) -> None:
        """Begin fresh with different prices for distinct products."""
        self.state = "fresh"
        self.calls: list[str] = []

    async def get_recent_preview(
        self, product_id: str, interval: CandleInterval, now: datetime
    ) -> MarketDataPreview:
        """Return synthetic preview states, never external market data."""
        self.calls.append(product_id)
        provider = ValuationProvider(Decimal("105" if product_id == "BTC-USD" else "55"))
        provider.state = self.state
        return await provider.get_recent_preview(product_id, interval, now)

    async def list_products(self) -> tuple[MarketProduct, ...]:
        """No catalog calls are needed."""
        return ()


async def interleaved_history(open_books: int) -> tuple[InMemoryExecutionStore, UUID]:
    """Interleave two complete round trips, then optionally open one or two books.

    BTC buys before ETH, but sells before ETH closes. A fungible cross-product
    fold incorrectly counts one round trip rather than the two recorded here.
    """
    store = InMemoryExecutionStore()
    deployment = _deployment(cash=Decimal("10000"))
    await store.create_deployment(deployment)
    events = [
        ("BTC-USD", OrderSide.BUY, "100", "0.1"),
        ("ETH-USD", OrderSide.BUY, "200", "0.2"),
        ("BTC-USD", OrderSide.SELL, "110", "0.1"),
        ("ETH-USD", OrderSide.SELL, "220", "0.2"),
    ]
    if open_books >= 1:
        events.append(("BTC-USD", OrderSide.BUY, "100", "0.1"))
    if open_books == 2:
        events.append(("LTC-USD", OrderSide.BUY, "50", "0.05"))
    cash = deployment.cash
    for hour, (product, side, price_text, fee_text) in enumerate(events, start=1):
        price, fee = Decimal(price_text), Decimal(fee_text)
        order = replace(
            _order(
                deployment_id=deployment.id,
                side=side,
                kind=OrderKind.POST_ONLY_LIMIT,
                price=price,
                client_order_id=f"recorded-{hour}",
            ),
            product_id=product,
        )
        await store.save_order(order)
        await store.save_fill(
            _fill(
                deployment_id=deployment.id,
                order_id=order.id,
                price=price,
                fee=fee,
                hour=hour,
            )
        )
        cash += (price if side is OrderSide.SELL else -price) - fee
        if hour >= 5:
            await store.save_position(
                Position(
                    deployment_id=deployment.id,
                    product_id=product,
                    quantity=Decimal("1"),
                    entry_price=price,
                    stop_price=price - Decimal("10"),
                    target_price=price + Decimal("20"),
                    entered_bar=_now() + timedelta(hours=hour),
                    updated_at=_now(),
                ),
                deployment_id=deployment.id,
            )
    await store.save_deployment(
        replace(deployment, cash=cash, phase=RuntimePhase.OPEN if open_books else RuntimePhase.FLAT)
    )
    return store, deployment.id


def cli_text(report: PerformanceReport, capsys: pytest.CaptureFixture[str]) -> str:
    """Render the HTTP-validated report using the shipped CLI text path."""
    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(
                matching_ready_payload(), report.model_dump(mode="json")
            ),
        ),
        pytest.raises(SystemExit) as exited,
    ):
        main(
            [
                "performance",
                "--deployment-id",
                str(report.payload.deployment_id),
                "--format",
                "text",
            ]
        )
    assert exited.value.code == (0 if report.payload.mark_complete else 1)
    return capsys.readouterr().out


def test_mixed_book_history_does_not_duplicate_shared_cash_profit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A closed row cannot add its realized profit to an open shared-cash projection."""
    store, deployment_id = asyncio.run(interleaved_history(1))
    report = http_and_cli(
        _diagnostics(execution=store, market_data=MarketDataService(RecordedPreviews())),
        deployment_id,
        capsys,
    )
    books = {book.product_id: book for book in report.payload.books}
    assert books["BTC-USD"].total_net_pnl == report.payload.total_net_pnl
    assert books["ETH-USD"].total_net_pnl is None
    assert books["ETH-USD"].realized_net_pnl == "19.6"


@pytest.mark.parametrize("open_books", [0, 1, 2])
@pytest.mark.parametrize("state", ["fresh", "stale", "empty", "failed"])
def test_recorded_stats_are_product_isolated_and_mark_independent(
    open_books: int, state: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Closed siblings survive all preview states and recovery without changing current PnL."""
    store, deployment_id = asyncio.run(interleaved_history(open_books))
    snapshot = asyncio.run(store.get_deployment(deployment_id))
    provider = RecordedPreviews()
    diagnostics = _diagnostics(execution=store, market_data=MarketDataService(provider))
    for observed_state in (state, "fresh"):
        provider.state = observed_state
        report = http_and_cli(diagnostics, deployment_id, capsys)
        payload = report.payload
        assert payload.trade_count == 2
        assert Decimal(payload.realized_net_pnl or "NaN") == Decimal("29.4")
        assert (
            Decimal(payload.total_fees or "NaN")
            == (Decimal("0.6"), Decimal("0.7"), Decimal("0.75"))[open_books]
        )
        marks = (
            {"BTC-USD": Decimal("105"), "LTC-USD": Decimal("55")}
            if observed_state == "fresh"
            else {}
        )
        existing = ledger_from_snapshot(snapshot, marks=marks)
        assert payload.total_net_pnl == existing.total_net_pnl_text()
        assert payload.total_return_fraction == existing.total_return_fraction_text()
        assert payload.maximum_drawdown_fraction == existing.maximum_drawdown_fraction_text()
        assert payload.mark_complete == existing.mark_complete
        assert payload.marked_exposure == (
            None if existing.marked_exposure is None else format(existing.marked_exposure, "f")
        )
        books = {book.product_id: book for book in payload.books}
        assert set(books) == (
            {"BTC-USD", "ETH-USD", "LTC-USD"} if open_books == 2 else {"BTC-USD", "ETH-USD"}
        )
        eth = books["ETH-USD"]
        assert eth.trade_count == 1
        assert eth.realized_net_pnl == "19.6"
        assert eth.total_fees == "0.4"
        assert eth.total_net_pnl == (None if open_books else "19.6")
        assert eth.mark_complete
        assert eth.valuation.status == "not_required"
        assert not eth.valuation.used
        assert eth.valuation.price is None
        assert eth.valuation.observed_at is None
        assert eth.valuation.candle_closed_at is None
        assert books["BTC-USD"].realized_net_pnl == "9.8"
        assert books["BTC-USD"].total_fees == ("0.3" if open_books else "0.2")
        for book in existing.books:
            assert books[book.product_id].total_net_pnl == (
                None if book.total_net_pnl is None else format(book.total_net_pnl, "f")
            )
        if open_books == 0:
            assert sum(
                (Decimal(book.total_net_pnl or "NaN") for book in books.values()), Decimal(0)
            ) == Decimal(payload.total_net_pnl or "NaN")
        text = cli_text(report, capsys)
        assert "Performance valuation channel: market_data_preview" in text
        assert "freshly observed preview" not in text
        assert "closed_trades=2; recorded_realized_net_pnl=29.4" in text
        assert (
            "ETH-USD recorded: closed_trades=1; recorded_realized_net_pnl=19.6; paid_fees=0.4"
            in text
        )
        assert "MUST NOT be summed" in text
    expected_calls = (
        ["BTC-USD", "LTC-USD"] if open_books == 2 else ["BTC-USD"] if open_books else []
    )
    assert provider.calls == expected_calls * 2
