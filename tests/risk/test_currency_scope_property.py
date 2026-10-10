"""Property: USD, USDC, USDT and CFM futures USD are never summed (ADR 0129 §1, P1-5).

Random fleets of paper books across every settlement scope are generated with a fixed seed.
For each proposed product, the capital, exposure and daily-loss paths must only ever see
books of the proposed product's scope, and losses in other scopes must never trip it.
"""

from __future__ import annotations

from decimal import Decimal
import random

from tests.risk.test_futures_gate_caps import _NOW, _book
from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.risk.breakers import (
    FUTURES_SCOPE,
    EntryObservation,
    _product_quote,
    evaluate_circuit_breakers,
    quote_scoped_snapshots,
)
from thytrader.risk.entry_limits import _capital_base
from thytrader.risk.futures_entry import futures_capital
from thytrader.risk.futures_policy import FuturesRiskPolicy
from thytrader.risk.models import RiskReasonCode, compiled_default_risk_policy
from thytrader.trading.models import DeploymentMode, DeploymentSnapshot

_PRODUCTS = (
    "BTC-USD",
    "ETH-USD",
    "BTC-USDC",
    "SOL-USDC",
    "BTC-USDT",
    "BIP-20DEC30-CDE",
    "ETP-20DEC30-CDE",
)
_POLICY = compiled_default_risk_policy().model_copy(
    update={
        "paper_capital_quote": "10000",
        "daily_loss_limit_fraction": "0.05",
        "futures": FuturesRiskPolicy(paper_capital_usd="7000"),
    }
)
_CASES = 400


def _fleet(rng: random.Random) -> tuple[DeploymentSnapshot, ...]:
    """One random fleet of flat paper books, each down up to 480 since today's open."""
    books = []
    for _index in range(rng.randint(1, 8)):
        product = rng.choice(_PRODUCTS)
        loss = rng.randint(0, 480)
        books.append(_book(product, cash=str(10000 - loss)))
    return tuple(books)


def _observation() -> EntryObservation:
    """Marks for every product (the books are flat, so marks never move equity)."""
    return EntryObservation(
        as_of=_NOW,
        proposed_price=Decimal(100),
        reference_price=Decimal(100),
        marks={product: Decimal(100) for product in _PRODUCTS},
    )


def _scope_loss(books: tuple[DeploymentSnapshot, ...], scope: str | None) -> Decimal:
    """The day's loss of the books in one scope, computed independently of the gate."""
    return sum(
        (
            Decimal(10000) - item.deployment.cash
            for item in books
            if _product_quote(item.deployment.product_id) == scope
        ),
        start=Decimal(0),
    )


def test_every_risk_path_stays_inside_one_settlement_scope() -> None:
    """Scoping, capital and the daily-loss breaker never cross USD, USDC, USDT or CFM-USD."""
    rng = random.Random(20261012)  # noqa: S311 - a reproducible test fleet, not a secret.
    crossed = 0
    for _case in range(_CASES):
        books = _fleet(rng)
        proposed = rng.choice(_PRODUCTS)
        scope = _product_quote(proposed)
        scoped, incomplete = quote_scoped_snapshots(books, proposed)
        assert incomplete is None
        assert all(_product_quote(item.deployment.product_id) == scope for item in scoped)
        assert len(scoped) == sum(
            1 for item in books if _product_quote(item.deployment.product_id) == scope
        )
        futures = is_futures_product_id(proposed)
        assert (scope == FUTURES_SCOPE) is futures
        capital = (
            futures_capital(_POLICY, DeploymentMode.PAPER)
            if futures
            else _capital_base(
                _POLICY, mode=DeploymentMode.PAPER, live_quote_cash=None, occupied=scoped
            )
        )
        assert capital == (Decimal(7000) if futures else Decimal(10000))
        verdict = evaluate_circuit_breakers(
            _POLICY,
            mode=DeploymentMode.PAPER,
            proposed_product_id=proposed,
            proposed_strategy_id=None,
            snapshots=books,
            observation=_observation(),
            capital=capital,
        )
        tripped = verdict is not None and verdict.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT
        own_loss = _scope_loss(books, scope)
        assert tripped is (own_loss >= capital * Decimal("0.05"))
        total = sum((Decimal(10000) - item.deployment.cash for item in books), start=Decimal(0))
        if total >= capital * Decimal("0.05") > own_loss:
            crossed += 1
    # The generator must actually exercise fleets whose summed loss would have tripped.
    assert crossed > 20
