"""Observer scope and display regressions use synthetic retained accounting evidence."""

from dataclasses import replace
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal, Inexact, localcontext
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from tests.alerts.test_futures_recovery_proof import proven_book
from tests.exchanges.test_coinbase_cfm import _transport
from tests.execution.test_paper_futures_books import _policy
from tests.operator_diagnostics.test_futures_account_report import _Store
from thytrader.alerts.models import AlertCode
from thytrader.alerts.supervision_futures import futures_safety_evidence
from thytrader.exchanges.coinbase_cfm import CoinbaseCfmAccount
from thytrader.exchanges.futures_models import margin_ratio
from thytrader.execution.futures_visibility import visible_position_comparison
from thytrader.execution.futures_visibility_fleet import load_live_futures_probes
from thytrader.operator.futures_books_live import live_payload
from thytrader.operator.futures_books_report import build_futures_books_report
from thytrader.operator.futures_classification import classify_positions
from thytrader.operator.readiness_futures import futures_section
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentStatus
from thytrader.worker.futures_mirror import observe_futures_account

if TYPE_CHECKING:
    from thytrader.operator.readiness_models import ReadinessFinding

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("numerator", "denominator", "expected"),
    [("1E10", "1", None), ("1E-20", "1", None), ("1", "3", "0.3333"), ("2", "1", "2")],
)
async def test_ratio_consumers_contain_saturation(
    numerator: str, denominator: str, expected: str | None
) -> None:
    """Disabled caller traps cannot manufacture a finite saturated account or book ratio."""
    book, account, now = await proven_book()
    receipt = await observe_futures_account(CoinbaseCfmAccount(_transport()), now)
    assert receipt.balance is not None
    balance = replace(
        receipt.balance,
        available_margin=Decimal(numerator),
        liquidation_threshold=Decimal(denominator),
    )
    account = replace(
        account,
        available_margin_usd=Decimal(numerator),
        liquidation_threshold_usd=Decimal(denominator),
    )
    with localcontext() as ctx:
        ctx.prec, ctx.Emax, ctx.Emin, ctx.rounding = 10, 4, -4, ROUND_DOWN
        for signal in ctx.traps:
            ctx.traps[signal] = False
        ctx.flags[Inexact] = True
        before = ctx.copy()
        projected = live_payload(
            book, account=account, observation=None, policy=None, books=(book,), now=now
        )
        ratio = margin_ratio(balance)
        assert projected.margin_ratio == expected
        assert ratio == (None if expected is None else Decimal(expected))
        assert ctx.flags == before.flags
        assert ctx.traps == before.traps


@pytest.mark.parametrize("strict", [False, True])
async def test_stopped_scope_arithmetic_never_escapes_or_retires(strict: bool) -> None:
    """An inexact stopped ledger cannot be retired or certified recovered by any observer."""
    book, account, now = await proven_book()
    book = replace(
        book,
        position=None,
        positions=(),
        fills=tuple(replace(f, quantity=Decimal(".01001")) for f in book.fills),
        deployment=replace(
            book.deployment,
            status=DeploymentStatus.STOPPED,
            futures_flat_confirmation_after=now - timedelta(seconds=1),
        ),
    )
    with localcontext() as ctx:
        ctx.prec = 3
        ctx.traps[Inexact] = strict
        before = ctx.copy()
        assert visible_position_comparison(book, account, books=(book,), now=now) is None
        rows = classify_positions((), snapshots=(book,), account=account, complete=True, now=now)
        assert len(rows) == 1
        assert rows[0].classification == "unknown"
        evidence = futures_safety_evidence((book,), account=account, policy=_policy(), now=now)
        assert any(f.code is AlertCode.FUTURES_ACCOUNT_UNKNOWN for f in evidence.findings)
        assert not evidence.evaluated
        assert ctx.flags == before.flags
        assert ctx.traps == before.traps


async def test_classifier_retains_full_accounting_evidence() -> None:
    """The direct helper matches shared proof despite an incomplete stopped sibling."""
    book, account, now = await proven_book()
    sibling, _, _ = await proven_book()
    sibling = replace(
        sibling,
        orders=(),
        intents=(),
        fills=(),
        position=None,
        positions=(),
        accounting_complete=False,
        deployment=replace(sibling.deployment, status=DeploymentStatus.STOPPED),
    )
    for complete in (False, True):
        books = (book, replace(sibling, accounting_complete=complete))
        row = classify_positions((), snapshots=books, account=account, complete=True, now=now)[0]
        assert row.classification == ("managed_matched" if complete else "unknown")


async def test_stopped_scope_observer_consumers_keep_unknown_inventory() -> None:
    """Readiness, list reports and fleet loading contain the same malformed stopped ledger."""
    book, _, now = await proven_book()
    book = replace(
        book,
        position=None,
        positions=(),
        fills=tuple(replace(f, quantity=Decimal(".01001")) for f in book.fills),
        deployment=replace(
            book.deployment,
            status=DeploymentStatus.STOPPED,
            futures_flat_confirmation_after=now - timedelta(seconds=1),
        ),
    )
    receipt = await observe_futures_account(CoinbaseCfmAccount(_transport()), now)
    execution = InMemoryExecutionStore()
    await execution.create_deployment(book.deployment)
    findings: list[ReadinessFinding] = []
    with localcontext() as ctx:
        ctx.prec = 3
        ctx.traps[Inexact] = True
        before = ctx.copy()
        section = await futures_section(
            _Store(receipt), findings, snapshots=(book,), now=now, policy=_policy()
        )
        assert section is not None and len(section.live_books) == 1
        assert any(f.reason_code == "POSITION_COMPARISON_UNPROVEN" for f in findings)
        venue, probes, _judged = await load_live_futures_probes(
            (book,), store=_Store(receipt), runtime=None, now=now
        )
        assert venue is None and probes == {}
        with patch.object(execution, "get_accounting_snapshot", new=AsyncMock(return_value=book)):
            report = await build_futures_books_report(
                execution=execution,
                journal=None,
                contracts=None,
                observations=None,
                policy=None,
                now=now,
            )
        assert report.overall_status.value == "degraded"
        assert len(report.payload.books) == 1
        assert report.payload.books[0].live is not None
        assert report.payload.books[0].live.match_state == "unknown"
        assert ctx.flags == before.flags
