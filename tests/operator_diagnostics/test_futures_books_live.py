"""Live futures read projections preserve provenance, scope and exact currencies."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, localcontext
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from tests.execution.test_paper_futures_books import _policy
from tests.operator_diagnostics.test_futures_books_report import _AT, _binding, _Latest, _snapshot
from thytrader.execution.futures_live import FuturesLiveAccount
from thytrader.operator import futures_books_report
from thytrader.operator.futures_books_report import futures_book_payload
from thytrader.risk.futures_live import FuturesVenueEvidence
from thytrader.trading.futures_book import InMemoryFuturesContractStore
from thytrader.trading.models import DeploymentMode, PositionSide

if TYPE_CHECKING:
    from thytrader.execution.decision_store import DecisionJournalStore


@pytest.mark.anyio
async def test_live_metrics_reprice_but_ownership_stays_unknown() -> None:
    """Reprice dependent metrics, but absent order coverage cannot prove a match."""
    snapshot = _snapshot(side=PositionSide.LONG, cash="-100")
    snapshot = replace(
        snapshot,
        deployment=replace(
            snapshot.deployment, mode=DeploymentMode.LIVE, allocated_capital=Decimal(100)
        ),
    )
    contracts = InMemoryFuturesContractStore()
    await contracts.bind_contract(_binding(snapshot.deployment.id))
    product = snapshot.deployment.product_id
    venue = FuturesVenueEvidence(
        observed_at=_AT,
        positions={product: Decimal(100)},
        contract_sizes={product: Decimal("0.01")},
        initial_margin_usd=Decimal(20),
        buying_power_usd=Decimal(100),
        external_order_products=None,
        killswitch_enabled=False,
        maintenance={},
    )
    account = FuturesLiveAccount(venue, {product: Decimal(110)}, Decimal(200), Decimal(100))
    policy = _policy()
    assert policy.futures is not None
    policy = policy.model_copy(
        update={
            "quote_currency": "USDC",
            "futures": policy.futures.model_copy(
                update={"live_spot_collateral_reserve_quote": "50", "peg_haircut": "1.1"}
            ),
        }
    )
    book = await futures_book_payload(
        snapshot,
        journal=None,
        contracts=contracts,
        observations=_Latest(),
        policy=policy,
        account=account,
        books=(snapshot,),
        now=_AT,
    )
    assert book.equity == "110"
    assert book.mark_price == "110"
    assert book.notional == "110"
    assert book.leverage == "1"
    assert book.initial_margin == "22"
    assert book.live is not None
    assert book.live.match_state == "unknown"
    assert book.live.margin_ratio == "2"
    assert book.live.reserve_required_usd == "22"
    assert book.live.reserve_headroom_quote == "28"
    assert book.live.reserve_currency == "USDC"
    assert book.live.venue_funding_pnl_usd is None
    stale = await futures_book_payload(
        snapshot,
        journal=None,
        contracts=contracts,
        observations=_Latest(),
        policy=policy,
        account=account,
        books=(snapshot,),
        now=_AT + timedelta(seconds=181),
    )
    assert stale.live is not None
    assert stale.live.venue_contracts is None
    assert stale.live.reserve_headroom_quote is None
    assert stale.live.match_state == "unknown"
    with localcontext() as context:
        context.prec = 2
        rounded = await futures_book_payload(
            snapshot,
            journal=None,
            contracts=contracts,
            observations=_Latest(),
            policy=policy,
            account=replace(account, venue=replace(venue, initial_margin_usd=Decimal("20.001"))),
            books=(snapshot,),
            now=_AT,
        )
    assert rounded.live is not None
    assert rounded.live.reserve_headroom_quote is None


@pytest.mark.anyio
async def test_admission_is_never_priced_at_the_journal_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A held book with no fresh venue mark is unknown evidence, whatever the journal says."""
    snapshot = _snapshot(side=PositionSide.LONG, cash="-100")
    snapshot = replace(
        snapshot,
        deployment=replace(
            snapshot.deployment, mode=DeploymentMode.LIVE, allocated_capital=Decimal(100)
        ),
    )
    contracts = InMemoryFuturesContractStore()
    await contracts.bind_contract(_binding(snapshot.deployment.id))
    product = snapshot.deployment.product_id
    venue = FuturesVenueEvidence(
        observed_at=_AT,
        positions={product: Decimal(100)},
        contract_sizes={product: Decimal("0.01")},
        initial_margin_usd=Decimal(20),
        buying_power_usd=Decimal(100),
        external_order_products=frozenset(),
        killswitch_enabled=False,
        maintenance={},
    )
    account = FuturesLiveAccount(venue, {}, Decimal(200), Decimal(100))

    async def journal_close(*_: object, **__: object) -> dict[str, SimpleNamespace]:
        return {product: SimpleNamespace(price=Decimal(105), bar_closes_at=_AT)}

    monkeypatch.setattr(futures_books_report, "last_bar_marks", journal_close)
    policy = _policy()
    assert policy.futures is not None
    policy = policy.model_copy(
        update={
            "futures": policy.futures.model_copy(
                update={
                    "live_enabled": True,
                    "live_capital_usd": "1000",
                    "product_allowlist": (product,),
                    "live_spot_collateral_reserve_quote": "1000",
                }
            )
        }
    )
    book = await futures_book_payload(
        snapshot,
        journal=cast("DecisionJournalStore", object()),
        contracts=contracts,
        observations=_Latest(),
        policy=policy,
        account=account,
        books=(snapshot,),
        now=_AT,
    )
    assert book.mark_price == "105"
    assert "FUTURES_COLLATERAL_UNKNOWN" in book.entry_blocks
