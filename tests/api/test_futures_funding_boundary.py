"""Exact nullable funding through real HTTP producers and the validated CLI consumer."""

import asyncio
from dataclasses import replace
from decimal import ROUND_DOWN, Decimal, Inexact, localcontext
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import pytest

from tests.alerts.test_futures_recovery_proof import proven_book
from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.operator.futures_books_report import FuturesBookPayload, FuturesBooksReport
from thytrader.operator.http import fetch_operator_report
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode, FundingCashFlow


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("mode", [DeploymentMode.LIVE, DeploymentMode.PAPER])
@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("1.0000000000000000000000000001", None),
        ("1E10", None),
        ("0", "0"),
        ("-0.25", "-0.25"),
        (None, "0"),
    ],
)
def test_public_funding_exact_or_unknown(
    strict: bool, mode: DeploymentMode, amount: str | None, expected: str | None
) -> None:
    """Both duplicated totals agree, preserving exact normal paper values and known zero."""
    book, _, now = asyncio.run(proven_book())
    book = replace(
        book,
        position=None,
        positions=(),
        orders=(),
        intents=(),
        fills=(),
        deployment=replace(
            book.deployment, mode=mode, cash=Decimal(0), allocated_capital=Decimal(100)
        ),
    )
    funding = (
        ()
        if amount is None
        else (
            FundingCashFlow(
                deployment_id=book.deployment.id,
                product_id=book.deployment.product_id,
                funding_time=now,
                signed_quantity=Decimal(1),
                mark_price=Decimal(100),
                rate=Decimal(".01"),
                amount=Decimal(amount),
                applied_at=now,
            ),
        )
    )
    book = replace(book, funding=funding)
    store = InMemoryExecutionStore()
    asyncio.run(store.create_deployment(book.deployment))
    app = create_app(Settings(_env_file=None), execution_store=store)
    with localcontext() as ctx:
        if amount == "1E10":
            ctx.prec, ctx.Emax, ctx.Emin, ctx.rounding = 10, 4, -4, ROUND_DOWN
        for signal in ctx.traps:
            ctx.traps[signal] = False
        ctx.traps[Inexact] = strict
        before = ctx.copy()
        with (
            patch.object(store, "get_accounting_snapshot", new=AsyncMock(return_value=book)),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            response = client.get("/api/v1/operator/futures-books")
            assert response.status_code == 200
            report = FuturesBooksReport.model_validate(response.json())
            detail = client.get(f"/api/v1/deployments/{book.deployment.id}/futures")
            assert detail.status_code == 200
            row = FuturesBookPayload.model_validate(detail.json())
        with patch("thytrader.operator.http.request_json", return_value=response.json()):
            cli = fetch_operator_report(base_url="http://127.0.0.1:18080", command="futures-books")
        assert isinstance(cli, FuturesBooksReport)
        for payload in (row, report.payload.books[0], cli.payload.books[0]):
            assert payload.funding_total == expected
            if expected is None:
                assert "funding" in payload.unknown
            if mode is DeploymentMode.LIVE:
                assert payload.live is not None
                assert payload.live.funding_accrued_usd == expected
                assert payload.live.funding_drift_reason == "SETTLEMENT_PROVENANCE_UNAVAILABLE"
        assert ctx.flags == before.flags
        assert ctx.traps == before.traps
