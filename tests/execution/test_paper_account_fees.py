"""New paper books default to the account's own fee rates and never to invented ones."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.execution.decision_support import Catalog, strategy
from tests.execution.test_discretionary import _request
from tests.portfolios.runtime_support import operator, portfolio, world
from thytrader.execution.discretionary import place_discretionary_order
from thytrader.execution.paper import PaperBroker
from thytrader.execution.paper_fees import (
    PaperFeesUnavailableError,
    paper_fee_rates,
    paper_fees_unavailable,
)
from thytrader.execution.service import create_deployment
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.portfolios.models import PortfolioConflictError
from thytrader.strategies.models import strategy_fingerprint
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode

pytestmark = pytest.mark.anyio
_ACCOUNT = (Decimal("0.005"), Decimal("0.009"))


class _Account:
    """Count reads of the account's rates, or refuse like an unreadable account."""

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.reads = 0

    async def __call__(self) -> tuple[Decimal, Decimal]:
        self.reads += 1
        if not self.available:
            raise paper_fees_unavailable("demo or missing credentials")
        return _ACCOUNT


async def _start(
    store: InMemoryExecutionStore,
    account: _Account,
    *,
    mode: DeploymentMode = DeploymentMode.PAPER,
    maker: str | None = None,
    taker: str | None = None,
) -> tuple[Decimal | None, Decimal | None]:
    """Start one book from a fresh strategy and return its stored paper rates."""
    definition = strategy()
    created = await create_deployment(
        store=store,
        publication_store=Catalog(definition),
        strategy_fingerprint=strategy_fingerprint(definition),
        mode=mode,
        paper_starting_cash=Decimal("10000") if mode is DeploymentMode.PAPER else None,
        live_allowed=mode is DeploymentMode.LIVE,
        paper_maker_fee_rate=None if maker is None else Decimal(maker),
        paper_taker_fee_rate=None if taker is None else Decimal(taker),
        paper_fee_source=account,
    )
    return created.paper_maker_fee_rate, created.paper_taker_fee_rate


async def test_a_paper_start_without_rates_stores_the_accounts_rates() -> None:
    """The book persists the account's rates explicitly, not a ledger default."""
    account = _Account()
    assert await _start(InMemoryExecutionStore(), account) == _ACCOUNT
    assert account.reads == 1


async def test_explicit_paper_rates_win_and_skip_the_account_read() -> None:
    """An operator's rates are used as given; the account is not consulted."""
    account = _Account(available=False)
    rates = await _start(InMemoryExecutionStore(), account, maker="0.004", taker="0.006")
    assert rates == (Decimal("0.004"), Decimal("0.006"))
    assert account.reads == 0


async def test_an_unreadable_account_refuses_the_paper_start_and_creates_nothing() -> None:
    """No invented fallback rates: the start fails and names the explicit-rate escape."""
    store = InMemoryExecutionStore()
    with pytest.raises(PaperFeesUnavailableError, match="--maker-fee-rate"):
        await _start(store, _Account(available=False))
    assert await store.list_deployments() == ()


async def test_partial_rates_are_left_for_validation_without_an_account_read() -> None:
    """One rate alone is still the caller's error, not a cue to mix in account rates."""
    account = _Account()
    assert await paper_fee_rates(
        maker_fee_rate=Decimal("0.004"), taker_fee_rate=None, source=account
    ) == (Decimal("0.004"), None)
    assert account.reads == 0


async def test_without_a_source_omitted_rates_stay_omitted() -> None:
    """Internal callers without an account keep the ledger's documented behavior."""
    assert await paper_fee_rates(maker_fee_rate=None, taker_fee_rate=None, source=None) == (
        None,
        None,
    )


async def test_a_paper_portfolio_start_gives_every_new_sleeve_the_accounts_rates() -> None:
    """Each new sleeve book stores the account's rates from one read."""
    state = world()
    current, _ = await portfolio(state)
    account = _Account()
    await state.runtime.start(
        current.portfolio.portfolio_id,
        revision=current.portfolio.revision,
        context=operator(),
        paper_fee_source=account,
    )
    books = await state.tagged(current.portfolio.portfolio_id)
    assert books
    assert {(book.paper_maker_fee_rate, book.paper_taker_fee_rate) for book in books} == {_ACCOUNT}
    assert account.reads == 1


async def test_an_unreadable_account_refuses_a_paper_portfolio_start_before_any_book() -> None:
    """The refusal is a portfolio conflict and no sleeve book is created."""
    state = world()
    current, _ = await portfolio(state)
    with pytest.raises(PortfolioConflictError) as raised:
        await state.runtime.start(
            current.portfolio.portfolio_id,
            revision=current.portfolio.revision,
            context=operator(),
            paper_fee_source=_Account(available=False),
        )
    assert raised.value.code == "paper_fees_unavailable"
    assert await state.tagged(current.portfolio.portfolio_id) == ()


async def test_a_new_discretionary_paper_book_takes_the_accounts_rates() -> None:
    """A discretionary ticket that creates a paper book stores the account's rates."""
    account = _Account()
    snapshot = await place_discretionary_order(
        store=InMemoryExecutionStore(),
        broker=PaperBroker(),
        market_data=MarketDataService(DemoMarketData()),
        request=_request(idempotency_key="account-fees"),
        live_allowed=False,
        paper_fee_source=account,
    )
    deployment = snapshot.deployment
    assert (deployment.paper_maker_fee_rate, deployment.paper_taker_fee_rate) == _ACCOUNT
    assert account.reads == 1


async def test_an_unreadable_account_refuses_a_new_discretionary_paper_book() -> None:
    """No book or intent is created when the account's rates are unknown."""
    store = InMemoryExecutionStore()
    with pytest.raises(PaperFeesUnavailableError):
        await place_discretionary_order(
            store=store,
            broker=PaperBroker(),
            market_data=MarketDataService(DemoMarketData()),
            request=_request(idempotency_key="no-account-fees"),
            live_allowed=False,
            paper_fee_source=_Account(available=False),
        )
    assert await store.list_deployments() == ()
