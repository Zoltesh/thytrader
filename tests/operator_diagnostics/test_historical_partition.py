"""Historical totals require a proven partition of unchanged deployment cash economics."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal, Inexact, Overflow, localcontext

import pytest

from tests.operator_diagnostics.test_deployment_performance import _diagnostics, _now
from tests.operator_diagnostics.test_recorded_performance import cli_text, interleaved_history
from tests.operator_diagnostics.test_valuation_books import http_and_cli
from thytrader.operator.diagnostics.performance import _recorded_totals_partition
from thytrader.trading.ledger import ledger_from_snapshot
from thytrader.trading.models import FundingCashFlow


@pytest.mark.parametrize("funding", ["-2.5", "2.5", "0"])
def test_flat_history_only_publishes_proven_cash_partition(
    funding: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Applied non-fill cash cannot silently disappear from purported book totals."""
    store, deployment_id = asyncio.run(interleaved_history(0))
    flow = FundingCashFlow(
        deployment_id=deployment_id,
        product_id="BTC-USD",
        funding_time=_now(),
        signed_quantity=Decimal("1"),
        mark_price=Decimal("100"),
        rate=Decimal("0.0001"),
        amount=Decimal(funding),
        applied_at=_now(),
    )
    applied, snapshot = asyncio.run(store.apply_funding_transaction(deployment_id, flows=(flow,)))
    assert applied
    report = http_and_cli(_diagnostics(execution=store), deployment_id, capsys)
    payload = report.payload
    assert payload.total_net_pnl == ledger_from_snapshot(snapshot).total_net_pnl_text()
    assert payload.realized_net_pnl == "29.4"
    assert payload.total_fees == "0.6"
    assert payload.trade_count == 2
    assert payload.mark_complete
    for book in payload.books:
        assert book.mark_complete
        assert book.valuation.status == "not_required"
        assert not book.valuation.used
        assert book.valuation.price is None
        assert book.valuation.observed_at is None
        assert book.valuation.candle_closed_at is None
        if Decimal(funding):
            assert book.total_net_pnl is None
        else:
            assert book.total_net_pnl == book.realized_net_pnl
    text = cli_text(report, capsys)
    if Decimal(funding):
        assert any("non-fill cash" in warning for warning in report.partial_result_warnings)
        assert "non-fill cash" in text
    else:
        assert not any("non-fill cash" in warning for warning in report.partial_result_warnings)
        assert sum(
            (Decimal(book.total_net_pnl or "NaN") for book in payload.books), Decimal(0)
        ) == Decimal(payload.total_net_pnl or "NaN")


@pytest.mark.parametrize("caller_traps", [False, True])
def test_partition_proof_rejects_rounding_without_touching_caller_context(
    *, caller_traps: bool
) -> None:
    """Rounded equality and finite-saturating overflow cannot certify a partition."""
    store, deployment_id = asyncio.run(interleaved_history(0))
    ledger = ledger_from_snapshot(asyncio.run(store.get_deployment(deployment_id)))
    with localcontext() as context:
        context.prec = 3
        context.Emax = 3
        context.traps[Inexact] = caller_traps
        context.traps[Overflow] = caller_traps
        context.clear_flags()
        before = (dict(context.flags), dict(context.traps))
        recorded = {
            "BTC-USD": replace(ledger, realized_net_pnl=Decimal("1")),
            "ETH-USD": replace(ledger, realized_net_pnl=Decimal("0.00001")),
        }
        assert not _recorded_totals_partition(replace(ledger, total_net_pnl=Decimal("1")), recorded)
        recorded["ETH-USD"] = replace(ledger, realized_net_pnl=Decimal("2"))
        assert _recorded_totals_partition(replace(ledger, total_net_pnl=Decimal("3")), recorded)
        recorded["ETH-USD"] = replace(ledger, realized_net_pnl=Decimal("1e10"))
        assert not _recorded_totals_partition(ledger, recorded)
        assert (dict(context.flags), dict(context.traps)) == before
