"""Adversarial coverage for shared proof and observer timing boundaries."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from tests.alerts.test_futures_recovery_proof import live_policy, proven_book
from tests.execution.test_paper_futures_books import _policy
from tests.operator_diagnostics.test_futures_books_report import _AT, _snapshot
from thytrader.alerts.models import AlertCode
from thytrader.alerts.supervision_futures import futures_safety_evidence
from thytrader.execution.futures_visibility import visible_position_comparison
from thytrader.trading.models import DeploymentMode, DeploymentStatus, OrderSide, PositionSide

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "boundary", ["intent_side", "duplicate_intent_order", "future_fill", "partial_sibling"]
)
async def test_shared_proof_rejects_inconsistent_ledger_and_portfolio(boundary: str) -> None:
    """Matching quantities cannot substitute for a complete, internally consistent ledger."""
    book, account, now = await proven_book()
    books = (book,)
    if boundary == "intent_side":
        book = replace(book, intents=tuple(replace(i, side=OrderSide.SELL) for i in book.intents))
    elif boundary == "duplicate_intent_order":
        book = replace(book, intents=(*book.intents, book.intents[0]))
    elif boundary == "future_fill":
        book = replace(book, fills=tuple(replace(f, filled_at=now) for f in book.fills))
    else:
        sibling, _, _ = await proven_book()
        sibling = replace(
            sibling,
            deployment=replace(sibling.deployment, product_id="ETP-20DEC30-CDE"),
            accounting_complete=False,
        )
        books = (book, sibling)
    if boundary != "partial_sibling":
        books = (book,)
    assert visible_position_comparison(book, account, books=books, now=now) is None


async def test_partial_stopped_inventory_cannot_be_authoritative_scope_removal() -> None:
    """An unreadable historical-looking book is not evidence that no managed obligations remain."""
    book = _snapshot(side=PositionSide.LONG, cash="0")
    book = replace(
        book,
        position=None,
        accounting_complete=False,
        deployment=replace(
            book.deployment, mode=DeploymentMode.LIVE, status=DeploymentStatus.STOPPED
        ),
    )
    evidence = futures_safety_evidence((book,), account=None, policy=_policy(), now=_AT)
    assert not evidence.evaluated
    assert any(f.code is AlertCode.FUTURES_ACCOUNT_UNKNOWN for f in evidence.findings)


@pytest.mark.parametrize("age", [0, 180, 181])
@pytest.mark.parametrize("failed", [False, True])
async def test_pending_grace_differs_from_failed_required_observation(
    age: int, failed: bool
) -> None:
    """A durable duty timestamps missing evidence; failure is immediate, never a pending grace."""
    book = _snapshot(side=PositionSide.LONG, cash="0")
    book = replace(
        book,
        position=None,
        deployment=replace(
            book.deployment,
            mode=DeploymentMode.LIVE,
            status=DeploymentStatus.STOPPED,
            futures_flat_confirmation_after=_AT,
        ),
    )
    evidence = futures_safety_evidence(
        (book,),
        account=None,
        policy=_policy(),
        now=_AT + timedelta(seconds=age),
        observation_failed=failed,
    )
    assert any(f.code is AlertCode.FUTURES_ACCOUNT_UNKNOWN for f in evidence.findings) == (
        failed or age > 180
    )
    assert not any(c.code is AlertCode.FUTURES_ACCOUNT_UNKNOWN for c in evidence.evaluated)


@pytest.mark.parametrize("age", [180, 181])
async def test_last_receipt_age_not_local_duty_age_controls_blindness(age: int) -> None:
    """New local writes cannot restart the freshness clock of the latest venue receipt."""
    book, account, now = await proven_book()
    now += timedelta(seconds=age)
    book = replace(book, deployment=replace(book.deployment, futures_flat_confirmation_after=now))
    evidence = futures_safety_evidence((book,), account=account, policy=live_policy(), now=now)
    assert any(f.code is AlertCode.FUTURES_ACCOUNT_UNKNOWN for f in evidence.findings) == (
        age > 180
    )
    assert not any(c.code is AlertCode.FUTURES_ACCOUNT_UNKNOWN for c in evidence.evaluated)


@pytest.mark.parametrize("seconds", [0, 1])
async def test_no_fill_flat_proof_requires_after_duty_not_invented_fill_grace(seconds: int) -> None:
    """The durable bound is strict; the five-second grace belongs to actual fills only."""
    book, account, now = await proven_book()
    book = replace(
        book,
        position=None,
        positions=(),
        fills=(),
        orders=(),
        intents=(),
        deployment=replace(book.deployment, futures_flat_confirmation_after=now),
    )
    account = replace(
        account,
        venue=replace(account.venue, observed_at=now + timedelta(seconds=seconds), positions={}),
    )
    result = visible_position_comparison(
        book, account, books=(book,), now=account.venue.observed_at
    )
    assert (result is not None and result.matches and result.book_contracts == Decimal(0)) == (
        seconds == 1
    )
