"""Whole-account CFM classification and gate proofs reject incomplete evidence."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from tests.execution.test_paper_futures_books import (
    _CONTRACT,
    _MARGIN,
    _PERP,
    _filled_book,
    _policy,
)
from tests.operator_diagnostics.test_futures_books_report import _AT, _deployment
from thytrader.execution.futures_live import FuturesLiveAccount
from thytrader.operator.futures_classification import classify_positions, managed_order_row
from thytrader.operator.venue_reconciliation_models import (
    ForeignOpenOrderRow,
    FuturesExternalPositionRow,
)
from thytrader.risk.futures_entry_probe import LiveFuturesProbe, live_probe_verdicts
from thytrader.risk.futures_live import FuturesVenueEvidence
from thytrader.trading.futures_book import BoundFuturesContract, FuturesBookState
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode, DeploymentSnapshot, RuntimePhase


@pytest.mark.anyio
async def test_order_ownership_requires_exact_persisted_identity() -> None:
    """Real stored intent/order pairs prove identity; prefixes and venue ids alone do not."""
    book = await _filled_book(InMemoryExecutionStore())
    book = replace(book, deployment=replace(book.deployment, mode=DeploymentMode.LIVE))
    order = book.orders[0]
    row = ForeignOpenOrderRow(
        venue_order_id=order.venue_order_id or "synthetic-venue",
        client_order_id=order.client_order_id,
        product_id=order.product_id,
        side="buy",
        status="OPEN",
    )
    assert managed_order_row(row, (book,))
    assert not managed_order_row(
        row.model_copy(update={"client_order_id": order.client_order_id + ":foreign"}), (book,)
    )
    assert not managed_order_row(row.model_copy(update={"client_order_id": None}), (book,))
    assert not managed_order_row(row.model_copy(update={"product_id": "ETP-20DEC30-CDE"}), (book,))
    assert not managed_order_row(row, (replace(book, intents=()),))


def test_complete_flat_comparison_and_unknown_coverage() -> None:
    """Neither stale, duplicate-owned, foreign-order nor unapplied evidence claims a match."""
    book = DeploymentSnapshot(
        deployment=replace(
            _deployment(cash="0"), mode=DeploymentMode.LIVE, phase=RuntimePhase.FLAT
        ),
        orders=(),
        fills=(),
        position=None,
    )
    venue = FuturesVenueEvidence(
        observed_at=_AT,
        positions={},
        contract_sizes={_PERP: Decimal("0.01")},
        initial_margin_usd=Decimal(0),
        buying_power_usd=Decimal(100),
        external_order_products=frozenset(),
        killswitch_enabled=False,
        maintenance={},
    )
    account = FuturesLiveAccount(venue, {}, Decimal(100), Decimal(20))
    rows = classify_positions((), snapshots=(book,), account=account, complete=True, now=_AT)
    assert len(rows) == 1
    assert rows[0].classification == "managed_matched"
    for changed, books, complete, now in [
        (account, (book,), False, _AT),
        (account, (book,), True, _AT + timedelta(seconds=181)),
        (account, (book, book), True, _AT),
        (replace(account, venue=replace(venue, external_order_products=None)), (book,), True, _AT),
        (
            replace(account, venue=replace(venue, external_order_products=frozenset({_PERP}))),
            (book,),
            True,
            _AT,
        ),
        (account, (replace(book, accounting_complete=False),), True, _AT),
    ]:
        result = classify_positions(
            (), snapshots=books, account=changed, complete=complete, now=now
        )
        assert result[0].classification == "unknown"
    foreign = FuturesExternalPositionRow(
        product_id="ETP-20DEC30-CDE", side="short", number_of_contracts="1"
    )
    assert (
        classify_positions((foreign,), snapshots=(), account=account, complete=False, now=_AT)[
            0
        ].classification
        == "unknown"
    )


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"killswitch_enabled": True}, "FUTURES_VENUE_KILLSWITCH"),
        ({"buying_power_usd": Decimal(0)}, "FUTURES_BUYING_POWER_SHORT"),
        ({"external_order_products": None}, "FUTURES_COLLATERAL_UNKNOWN"),
        ({"external_order_products": frozenset({_PERP})}, "FUTURES_EXTERNAL_POSITION_ON_PRODUCT"),
        ({"contract_sizes": {_PERP: Decimal("0.1")}}, "FUTURES_CONTRACT_DRIFT"),
    ],
)
def test_visibility_probe_runs_real_live_gate(change: dict[str, object], code: str) -> None:
    """The same minimum-contract gate used by fleet and book reports names venue denials."""
    book = DeploymentSnapshot(
        deployment=replace(
            _deployment(cash="0"), mode=DeploymentMode.LIVE, phase=RuntimePhase.FLAT
        ),
        orders=(),
        fills=(),
        position=None,
    )
    policy = _policy()
    assert policy.futures is not None
    policy = policy.model_copy(
        update={
            "futures": policy.futures.model_copy(
                update={
                    "live_enabled": True,
                    "live_capital_usd": "1000",
                    "product_allowlist": (_PERP,),
                    "live_spot_collateral_reserve_quote": "1000",
                }
            )
        }
    )
    venue = FuturesVenueEvidence(
        observed_at=_AT,
        positions={},
        contract_sizes={_PERP: Decimal("0.01")},
        initial_margin_usd=Decimal(0),
        buying_power_usd=Decimal(100),
        external_order_products=frozenset(),
        killswitch_enabled=False,
        maintenance={},
    )
    # dataclasses.replace validates every supplied synthetic venue field.
    venue = replace(venue, **change)
    probe = LiveFuturesProbe(
        FuturesBookState(
            deployment_id=book.deployment.id,
            product_id=_PERP,
            side="long",
            binding=BoundFuturesContract(book.deployment.id, _CONTRACT, Decimal("0.1"), _AT),
            margin=_MARGIN,
        ),
        Decimal(100),
    )
    verdicts = live_probe_verdicts(
        policy, book=book, probe=probe, snapshots=(book,), venue=venue, now=_AT
    )
    assert {verdict.reason_code.value for verdict in verdicts} == {code}
