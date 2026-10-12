"""Recovery requires the same complete ownership proof as read-only reports."""

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from tests.alerts.test_alert_service import _service
from tests.execution.test_paper_futures_books import _PERP, _filled_book, _policy
from thytrader.alerts.models import AlertCode
from thytrader.alerts.store import InMemoryAlertStore
from thytrader.alerts.supervision_futures import futures_safety_evidence
from thytrader.execution.futures_live import FuturesLiveAccount
from thytrader.operator.futures_books_live import live_payload
from thytrader.operator.futures_classification import classify_positions
from thytrader.risk.futures_live import FuturesVenueEvidence
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentKind, DeploymentMode, DeploymentSnapshot, OrderStatus

if TYPE_CHECKING:
    from thytrader.alerts.store import AlertStore
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.trading.store import ExecutionStore

pytestmark = pytest.mark.anyio


def live_policy() -> RiskPolicyDefinition:
    """An explicit synthetic margin policy for positive held-book recovery controls."""
    policy = _policy()
    assert policy.futures is not None
    return policy.model_copy(
        update={"futures": policy.futures.model_copy(update={"live_derisk_margin_ratio": "2"})}
    )


async def proven_book() -> tuple[DeploymentSnapshot, FuturesLiveAccount, datetime]:
    """Create real canonical fills, retaining terminal orders and their persisted intents."""
    book = await _filled_book(InMemoryExecutionStore())
    orders = tuple(order for order in book.orders if order.status is OrderStatus.FILLED)
    book = replace(
        book,
        deployment=replace(
            book.deployment,
            mode=DeploymentMode.LIVE,
            kind=DeploymentKind.DISCRETIONARY,
            strategy_fingerprint=None,
            strategy_id=None,
            paper_starting_cash=None,
            paper_maker_fee_rate=None,
            paper_taker_fee_rate=None,
        ),
        orders=orders,
        intents=tuple(i for i in book.intents if any(o.intent_id == i.id for o in orders)),
    )
    now = max(fill.filled_at for fill in book.fills) + timedelta(seconds=10)
    assert book.position is not None
    venue = FuturesVenueEvidence(
        observed_at=now,
        buying_power_usd=Decimal(100),
        initial_margin_usd=Decimal(20),
        positions={_PERP: book.position.quantity / Decimal(".01")},
        external_order_products=frozenset(),
        killswitch_enabled=False,
        maintenance={},
        contract_sizes={_PERP: Decimal(".01")},
    )
    return book, FuturesLiveAccount(venue, {}, Decimal(100), Decimal(20)), now


@pytest.mark.parametrize(
    "boundary", ["orders_unknown", "foreign", "duplicate", "intents", "partial"]
)
async def test_unknown_proof_cannot_clear_alerts_then_valid_proof_recovers(boundary: str) -> None:
    """Restart preserves I9/account/external alerts until every ownership fact is proved."""
    await recovery_scenario(boundary, InMemoryAlertStore())


async def recovery_scenario(
    boundary: str, store: AlertStore, execution: ExecutionStore | None = None
) -> None:
    """Exercise identical durable proof transitions against memory and PostgreSQL."""
    good, account, now = await proven_book()
    good = replace(
        good,
        deployment=replace(
            good.deployment, mismatch_detail="FUTURES_POSITION_MISMATCH: synthetic pause"
        ),
    )
    if execution is not None:
        await execution.create_deployment(good.deployment)
    book = good
    books = (book,)
    changed = account
    if boundary == "orders_unknown":
        changed = replace(account, venue=replace(account.venue, external_order_products=None))
    elif boundary == "foreign":
        changed = replace(
            account, venue=replace(account.venue, external_order_products=frozenset({_PERP}))
        )
    elif boundary == "duplicate":
        other, _, _ = await proven_book()
        books = (book, other)
    elif boundary == "intents":
        book = replace(book, intents=())
        books = (book,)
    else:
        book = replace(book, accounting_complete=False)
        books = (book,)
    missing = futures_safety_evidence(
        (good,), account=None, policy=live_policy(), now=now, observation_failed=True
    )
    await _service(store).apply(missing.findings, evaluated=missing.evaluated, now=now)
    foreign = futures_safety_evidence(
        (good,),
        account=replace(
            account, venue=replace(account.venue, external_order_products=frozenset({_PERP}))
        ),
        policy=live_policy(),
        now=now,
    )
    await _service(store).apply(foreign.findings, evaluated=foreign.evaluated, now=now)
    evidence = futures_safety_evidence(
        books, account=changed, policy=live_policy(), now=now + timedelta(seconds=1)
    )
    await _service(store).apply(
        evidence.findings, evaluated=evidence.evaluated, now=now + timedelta(seconds=1)
    )
    assert {
        AlertCode.FUTURES_POSITION_MISMATCH,
        AlertCode.FUTURES_ACCOUNT_UNKNOWN,
        AlertCode.FUTURES_EXTERNAL_ON_MANAGED_PRODUCT,
    } <= {a.code for a in await store.list_open_alerts()}
    assert (
        live_payload(
            book, account=changed, observation=None, policy=live_policy(), books=books, now=now
        ).match_state
        == "unknown"
    )
    assert (
        classify_positions((), snapshots=books, account=changed, complete=True, now=now)[
            0
        ].classification
        == "unknown"
    )
    recovery = futures_safety_evidence(
        (good,), account=account, policy=live_policy(), now=now + timedelta(seconds=2)
    )
    await _service(store).apply(
        recovery.findings, evaluated=recovery.evaluated, now=now + timedelta(seconds=2)
    )
    assert not await store.list_open_alerts()
