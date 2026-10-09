"""Venue reconciliation of adopted inventory against the held balance (ADR 0124)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.adoption_support import adopted_book
from tests.operator_diagnostics.test_readiness_preflight import ScriptedExchange, _balance
from thytrader.operator.venue_reconciliation import build_venue_reconciliation_report
from thytrader.portfolio.service import PortfolioService
from thytrader.trading.memory import InMemoryExecutionStore

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("venue_doge", "classification", "foreign"),
    [("150", "external_inventory", "50"), ("100", "matched", "0")],
)
async def test_adopted_quantity_is_managed_and_only_the_rest_is_external(
    venue_doge: str, classification: str, foreign: str
) -> None:
    """Adoption shrinks EXTERNAL_INVENTORY; its FILLED order is never a missing venue order."""
    store = InMemoryExecutionStore()
    await adopted_book(store)
    service = PortfolioService(
        ScriptedExchange((_balance("USD", "1000"), _balance("DOGE", venue_doge)))
    )
    report = await build_venue_reconciliation_report(portfolio=service, execution=store)
    doge = next(row for row in report.payload.assets if row.currency == "DOGE")
    assert doge.classification == classification
    assert doge.managed_net_quantity is not None
    assert Decimal(doge.managed_net_quantity) == Decimal(100)
    assert doge.foreign_quantity is not None and Decimal(doge.foreign_quantity) == Decimal(foreign)
    codes = {finding.reason_code for finding in report.payload.findings}
    assert "MANAGED_ORDER_NOT_AT_VENUE" not in codes
    assert ("EXTERNAL_INVENTORY" in codes) is (classification == "external_inventory")
