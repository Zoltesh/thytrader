"""Futures safety evidence stays read-only and never treats unknown as recovery."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, Inexact, localcontext

import pytest

from tests.alerts.test_alert_service import _service
from tests.alerts.test_futures_recovery_proof import live_policy, proven_book
from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_paper_futures_books import _policy
from tests.operator_diagnostics.test_futures_books_report import _AT, _snapshot
from thytrader.alerts import supervision_futures
from thytrader.alerts.models import AlertCode, AlertSeverity
from thytrader.alerts.store import InMemoryAlertStore
from thytrader.trading.models import DeploymentMode, PositionSide

pytestmark = pytest.mark.anyio


async def test_unknown_account_persists_across_restart_until_fresh() -> None:
    """A held live book with no margin evidence raises and only a fresh read clears."""
    book, account, stamp = await proven_book()
    now = stamp + timedelta(minutes=4)
    store = InMemoryAlertStore()
    for stamp in (now, now + timedelta(seconds=1)):
        evidence = supervision_futures.futures_safety_evidence(
            (book,), account=None, policy=_policy(), now=stamp
        )
        await _service(store).apply(evidence.findings, evaluated=evidence.evaluated, now=stamp)
    alerts = await store.list_open_alerts()
    assert len(alerts) == 1
    assert alerts[0].code is AlertCode.FUTURES_ACCOUNT_UNKNOWN
    assert alerts[0].subject == "futures:live"
    assert alerts[0].severity is AlertSeverity.CRITICAL
    stamp = now + timedelta(seconds=2)
    account = replace(account, venue=replace(account.venue, observed_at=stamp))
    recovered = supervision_futures.futures_safety_evidence(
        (book,), account=account, policy=live_policy(), now=stamp
    )
    await _service(store).apply(recovered.findings, evaluated=recovered.evaluated, now=stamp)
    assert not await store.list_open_alerts()


@pytest.mark.parametrize(
    ("available", "severity"),
    [("80", None), ("79.9999", "warning"), ("40", "critical"), ("-1", "critical")],
)
async def test_margin_thresholds_are_exact(available: str, severity: str | None) -> None:
    """Warning is below twice policy ratio; critical includes equality and negatives."""
    book = _snapshot(side=PositionSide.LONG, cash="0")
    book = replace(book, deployment=replace(book.deployment, mode=DeploymentMode.LIVE))
    venue = FakeVenue()
    venue.now = _AT
    venue.margin = Decimal(available)
    venue.threshold = Decimal(20)
    account = await venue.read_account(frozenset())
    policy = _policy()
    assert policy.futures is not None
    policy = policy.model_copy(
        update={"futures": policy.futures.model_copy(update={"live_derisk_margin_ratio": "2"})}
    )
    with localcontext() as context:
        context.traps[Inexact] = True
        evidence = supervision_futures.futures_safety_evidence(
            (book,), account=account, policy=policy, now=_AT
        )
    found = [row for row in evidence.findings if row.code is AlertCode.FUTURES_MARGIN_LOW]
    assert [row.severity.value for row in found] == ([] if severity is None else [severity])


@pytest.mark.parametrize("age", [181, -1])
async def test_stale_or_future_account_cannot_clear_margin(age: int) -> None:
    """Bad timestamps are unknown, not recovered or fresh zero margin."""
    book = _snapshot(side=PositionSide.LONG, cash="0")
    book = replace(book, deployment=replace(book.deployment, mode=DeploymentMode.LIVE))
    venue = FakeVenue()
    venue.now = _AT - timedelta(seconds=age)
    evidence = supervision_futures.futures_safety_evidence(
        (book,), account=await venue.read_account(frozenset()), policy=_policy(), now=_AT
    )
    assert not any(row.code is AlertCode.FUTURES_MARGIN_LOW for row in evidence.evaluated)
    assert any(row.code is AlertCode.FUTURES_ACCOUNT_UNKNOWN for row in evidence.findings)


async def test_mismatch_pause_only_clears_on_valid_match() -> None:
    """The I9 pause is durable evidence; an unreadable venue never resolves it."""
    book, account, now = await proven_book()
    book = replace(
        book,
        deployment=replace(
            book.deployment,
            mode=DeploymentMode.LIVE,
            mismatch_detail="FUTURES_POSITION_MISMATCH: synthetic mismatch",
        ),
    )
    bad = supervision_futures.futures_safety_evidence(
        (book,), account=None, policy=_policy(), now=_AT
    )
    assert any(row.code is AlertCode.FUTURES_POSITION_MISMATCH for row in bad.findings)
    good = supervision_futures.futures_safety_evidence(
        (book,), account=account, policy=_policy(), now=now
    )
    assert any(row.code is AlertCode.FUTURES_POSITION_MISMATCH for row in good.evaluated)
    assert not any(row.code is AlertCode.FUTURES_POSITION_MISMATCH for row in good.findings)


async def test_external_order_on_managed_product_requires_complete_fresh_read() -> None:
    """Exact-client-id classification from the source raises I3 without touching orders."""
    book = _snapshot(side=PositionSide.LONG, cash="0")
    book = replace(book, deployment=replace(book.deployment, mode=DeploymentMode.LIVE))
    venue = FakeVenue()
    venue.now = _AT
    venue.external = frozenset({book.deployment.product_id})
    evidence = supervision_futures.futures_safety_evidence(
        (book,), account=await venue.read_account(frozenset()), policy=_policy(), now=_AT
    )
    assert any(
        row.code is AlertCode.FUTURES_EXTERNAL_ON_MANAGED_PRODUCT for row in evidence.findings
    )
    venue.external = None
    unknown = supervision_futures.futures_safety_evidence(
        (book,), account=await venue.read_account(frozenset()), policy=_policy(), now=_AT
    )
    assert not any(
        row.code is AlertCode.FUTURES_EXTERNAL_ON_MANAGED_PRODUCT for row in unknown.evaluated
    )


async def test_foreign_holdings_never_clear_a_mismatch() -> None:
    """A matching primary product cannot hide unaccounted sibling inventory."""
    book = _snapshot(side=PositionSide.LONG, cash="0")
    assert book.position is not None
    book = replace(
        book,
        deployment=replace(book.deployment, mode=DeploymentMode.LIVE),
        positions=(book.position, replace(book.position, product_id="ETH-USD")),
    )
    venue = FakeVenue()
    venue.now = _AT
    venue.net = Decimal(100)
    evidence = supervision_futures.futures_safety_evidence(
        (book,),
        account=await venue.read_account(frozenset()),
        policy=_policy(),
        now=_AT,
    )
    assert not any(row.code is AlertCode.FUTURES_POSITION_MISMATCH for row in evidence.evaluated)


async def test_fresh_confirmed_flat_can_clear_account_and_margin_alerts() -> None:
    """Known zero on a flat account is distinct from absent threshold evidence."""
    book = _snapshot(side=PositionSide.LONG, cash="0")
    book = replace(
        book, position=None, deployment=replace(book.deployment, mode=DeploymentMode.LIVE)
    )
    venue = FakeVenue()
    venue.now = _AT
    venue.net = Decimal(0)
    venue.threshold = Decimal(0)
    evidence = supervision_futures.futures_safety_evidence(
        (book,),
        account=await venue.read_account(frozenset()),
        policy=_policy(),
        now=_AT,
    )
    assert {AlertCode.FUTURES_ACCOUNT_UNKNOWN, AlertCode.FUTURES_MARGIN_LOW} <= {
        row.code for row in evidence.evaluated
    }


async def test_paper_has_no_live_safety_findings() -> None:
    """Paper exposure never raises live account blindness or margin alerts."""
    book = _snapshot(side=PositionSide.LONG, cash="0")
    evidence = supervision_futures.futures_safety_evidence(
        (book,), account=None, policy=_policy(), now=_AT + timedelta(hours=1)
    )
    assert not evidence.findings
