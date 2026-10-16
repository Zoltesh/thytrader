"""The acknowledged live futures start over HTTP (ADR 0134 P2-7).

A live futures start needs ``i_understand_live`` and ``i_understand_live_futures``, a
``fee_per_contract`` and its ``futures_capital_usd``. The risk gate admits it on one fresh,
synthetic CFM read: policy opt-in, the USD envelope, one book per product, no external position
or order on the product, the catalog contract size and a perp. Every refusal creates nothing.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from tests.api.test_paper_futures_runtime_lane import _OPEN, _Catalog
from tests.execution.test_paper_futures_books import _PERP, _observation, _policy, _strategy
from tests.market_history import listed_history
from thytrader.api.app import create_app
from thytrader.api.routes import deployments
from thytrader.config import Settings
from thytrader.evaluation.futures_spec import contract_from_observation
from thytrader.execution import futures_live_start, futures_start, service
from thytrader.execution.futures_live import FuturesLiveAccount
from thytrader.execution.futures_start import FuturesStart, FuturesStartTerms
from thytrader.execution.paper import PaperBroker
from thytrader.market_data.models import Candle
from thytrader.risk.futures_live import FuturesVenueEvidence
from thytrader.risk.futures_policy import FuturesRiskPolicy
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.strategies.library import create_strategy_from_definition
from thytrader.strategies.memory_store import InMemoryStrategyStore
from thytrader.trading.futures_book import InMemoryFuturesContractStore
from thytrader.trading.ids import uuid7
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentStatus,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from thytrader.market_data.service import MarketDataService
    from thytrader.risk.models import RiskPolicyDefinition

_SIZE = Decimal(contract_from_observation(_observation()).contract_size)


class _Source:
    """One synthetic CFM read at the start instant; fields are set per test."""

    def __init__(self) -> None:
        """A flat, funded account with no foreign orders."""
        self.positions: dict[str, Decimal] = {}
        self.external: frozenset[str] = frozenset()
        self.reads = 0
        self.delay = 0.0
        self.managed: frozenset[tuple[str, str]] = frozenset()
        self.observed_at = _OPEN
        self.killswitch: bool | None = False

    async def read_account(
        self,
        managed_client_ids: frozenset[tuple[str, str]],
        product_ids: frozenset[str] = frozenset(),
    ) -> FuturesLiveAccount:
        """Return fresh evidence for the perp after ``delay`` seconds."""
        del product_ids
        self.managed = managed_client_ids
        self.reads += 1
        await asyncio.sleep(self.delay)
        return FuturesLiveAccount(
            venue=FuturesVenueEvidence(
                observed_at=self.observed_at,
                buying_power_usd=Decimal(5000),
                initial_margin_usd=Decimal(0),
                positions=dict(self.positions),
                external_order_products=self.external,
                killswitch_enabled=self.killswitch,
                maintenance={},
                contract_sizes={_PERP: _SIZE},
            ),
            marks={},
            available_margin_usd=Decimal(5000),
            liquidation_threshold_usd=Decimal(0),
        )


@dataclass(frozen=True, slots=True)
class _Live:
    """One credentialed app and what a start writes."""

    client: TestClient
    strategy_id: UUID
    execution: InMemoryExecutionStore
    contracts: InMemoryFuturesContractStore
    source: _Source
    strategies: InMemoryStrategyStore


def _live_policy(**changes: Any) -> RiskPolicyDefinition:
    """The paper futures policy plus a live opt-in for the perp with a 1000 USD envelope."""
    futures = FuturesRiskPolicy(
        paper_capital_usd="100000",
        live_enabled=True,
        live_capital_usd="1000",
        product_allowlist=(_PERP,),
        live_spot_collateral_reserve_quote="1000",
    ).model_copy(update=changes)
    return _policy().model_copy(update={"futures": futures})


def _app(
    monkeypatch: pytest.MonkeyPatch, policy: RiskPolicyDefinition, *, source: _Source | None
) -> _Live:
    """A credentialed app with ``policy`` published and ``source`` as the start evidence."""
    for module in (service, futures_start, futures_live_start):
        monkeypatch.setattr(module, "utc_now", lambda: _OPEN)

    async def close(market_data: object, product_id: str) -> Decimal | None:
        """The demo market data has no perp candles; the perp last closed at 100 USD."""
        del market_data
        return Decimal(100) if product_id == _PERP else None

    monkeypatch.setattr(futures_live_start, "_latest_close", close)
    strategy = _strategy()
    strategies = InMemoryStrategyStore()
    asyncio.run(create_strategy_from_definition(strategies, strategy))
    risk = InMemoryRiskPolicyStore()
    asyncio.run(risk.publish(policy))
    execution = InMemoryExecutionStore()
    contracts = InMemoryFuturesContractStore()
    app = create_app(
        Settings(
            _env_file=None,
            coinbase_api_key_name=SecretStr("key"),
            coinbase_api_private_key=SecretStr("secret"),
        ),
        strategy_store=strategies,
        execution_store=execution,
        risk_policy_store=risk,
        paper_broker=PaperBroker(),
        live_broker=PaperBroker(),
        market_data_service=listed_history(),
    )
    app.state.futures_start = FuturesStart(
        contracts=contracts, observations=_Catalog(), live_source=source
    )
    return _Live(
        TestClient(app), strategy.strategy_id, execution, contracts, source or _Source(), strategies
    )


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Live]:
    """The opted-in app with a clean synthetic account."""
    lane = _app(monkeypatch, _live_policy(), source=_Source())
    with lane.client:
        yield lane


def _body(strategy_id: UUID, **changes: object) -> dict[str, object]:
    """A complete live futures start."""
    body: dict[str, object] = {
        "strategy_id": str(strategy_id),
        "mode": "live",
        "i_understand_live": True,
        "i_understand_live_futures": True,
        "fee_per_contract": "0.11",
        "futures_capital_usd": "500",
    }
    body.update(changes)
    return {key: value for key, value in body.items() if value is not None}


def test_an_acknowledged_live_futures_start_creates_a_capitalised_book(live: _Live) -> None:
    """The capital is the book's allocation and performance basis; the contract is bound."""
    started = live.client.post("/api/v1/deployments", json=_body(live.strategy_id))
    assert started.status_code == 201, started.text
    deployment = live.execution.deployments[UUID(started.json()["id"])]
    assert deployment.mode is DeploymentMode.LIVE
    assert deployment.product_id == _PERP
    assert deployment.allocated_capital == Decimal(500)
    assert deployment.performance_capital_quote == Decimal(500)
    assert deployment.cash == Decimal(0)
    binding = live.contracts.bindings[deployment.id]
    assert binding.fee_per_contract == Decimal("0.11")
    assert live.source.reads == 1


def test_a_live_futures_start_needs_its_own_acknowledgement(live: _Live) -> None:
    """``i_understand_live`` alone is a 428 naming LIVE_FUTURES_ACK_REQUIRED."""
    refused = live.client.post(
        "/api/v1/deployments", json=_body(live.strategy_id, i_understand_live_futures=None)
    )
    assert refused.status_code == 428
    assert refused.json()["detail"].startswith("LIVE_FUTURES_ACK_REQUIRED")
    assert live.execution.deployments == {}


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"futures_capital_usd": None}, "FUTURES_CAPITAL_REQUIRED"),
        ({"futures_capital_usd": "0"}, "FUTURES_CAPITAL_REQUIRED"),
        ({"fee_per_contract": None}, "FUTURES_FEE_REQUIRED"),
        ({"futures_capital_usd": "1500"}, "FUTURES_LIVE_CAPITAL_EXCEEDED"),
        ({"paper_fee_per_contract": "0.11"}, "paper_fee_per_contract is for paper futures"),
    ],
)
def test_incomplete_or_oversized_terms_are_refused(
    live: _Live, changes: dict[str, object], code: str
) -> None:
    """Each start term is required and checked before anything is written."""
    refused = live.client.post("/api/v1/deployments", json=_body(live.strategy_id, **changes))
    assert refused.status_code == 409
    assert refused.json()["detail"].startswith(code)
    assert live.execution.deployments == {}
    assert live.contracts.bindings == {}


@pytest.mark.parametrize(
    ("positions", "external", "code"),
    [
        ({_PERP: Decimal(1)}, frozenset(), "FUTURES_EXTERNAL_POSITION_ON_PRODUCT"),
        ({}, frozenset({_PERP}), "FUTURES_EXTERNAL_POSITION_ON_PRODUCT"),
    ],
)
def test_the_product_must_be_ours_alone_at_start(
    live: _Live, positions: dict[str, Decimal], external: frozenset[str], code: str
) -> None:
    """I3: a position or open order on the product that ThyTrader does not manage refuses."""
    live.source.positions = positions
    live.source.external = external
    refused = live.client.post("/api/v1/deployments", json=_body(live.strategy_id))
    assert refused.status_code == 409
    assert refused.json()["detail"].startswith(code)
    assert live.execution.deployments == {}


@pytest.mark.parametrize(
    ("policy", "source", "code"),
    [
        (_live_policy(live_enabled=False), _Source(), "FUTURES_LIVE_DISABLED"),
        (_live_policy(product_allowlist=()), _Source(), "PRODUCT_NOT_ALLOWLISTED"),
        (
            _live_policy(live_spot_collateral_reserve_quote=None),
            _Source(),
            "FUTURES_COLLATERAL_RESERVE_SHORT",
        ),
        (_live_policy(), None, "FUTURES_COLLATERAL_UNKNOWN"),
    ],
)
def test_policy_opt_in_and_venue_evidence_are_required(
    monkeypatch: pytest.MonkeyPatch,
    policy: RiskPolicyDefinition,
    source: _Source | None,
    code: str,
) -> None:
    """Live futures are off by default; without a CFM read the evidence is unknown."""
    lane = _app(monkeypatch, policy, source=source)
    with lane.client:
        refused = lane.client.post("/api/v1/deployments", json=_body(lane.strategy_id))
    assert refused.status_code == 409
    assert refused.json()["detail"].startswith(code), refused.text
    assert lane.execution.deployments == {}


def test_a_second_live_book_on_the_product_is_refused(live: _Live) -> None:
    """I3: one live futures book per product."""
    first = live.client.post("/api/v1/deployments", json=_body(live.strategy_id))
    assert first.status_code == 201, first.text
    deployment = live.execution.deployments[UUID(first.json()["id"])]
    other = replace(deployment, id=UUID(int=1), strategy_id=UUID(int=2))
    asyncio.run(live.execution.create_deployment(other))
    del live.execution.deployments[deployment.id]
    again = live.client.post("/api/v1/deployments", json=_body(live.strategy_id))
    assert again.status_code == 409
    assert again.json()["detail"].startswith("FUTURES_PRODUCT_OCCUPIED")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("rate", "price", "expected"),
    [
        (Decimal("0.25"), Decimal(100), _SIZE * 100 * Decimal("0.25")),
        (None, Decimal(100), None),
        (Decimal("0.25"), None, None),
    ],
)
async def test_the_start_margin_is_one_contract_or_unknown(
    monkeypatch: pytest.MonkeyPatch,
    rate: Decimal | None,
    price: Decimal | None,
    expected: Decimal | None,
) -> None:
    """One contract at the latest close times the higher overnight rate; unknown stays None."""

    async def close(market_data: object, product_id: str) -> Decimal | None:
        del market_data, product_id
        return price

    monkeypatch.setattr(futures_live_start, "_latest_close", close)
    terms = FuturesStartTerms(
        contract=contract_from_observation(_observation()),
        fee_per_contract=Decimal("0.11"),
        initial_margin_rate=rate,
    )
    margin = await futures_live_start._one_contract_margin(terms, listed_history())
    assert margin == expected


def test_concurrent_live_futures_starts_cannot_both_pass_exclusivity(live: _Live) -> None:
    """Two strategies racing on one perp: the start lock admits one, the other is occupied."""
    second = _strategy().model_copy(update={"strategy_id": uuid7(_OPEN), "name": "Second perp"})
    asyncio.run(create_strategy_from_definition(live.strategies, second))
    live.source.delay = 0.2
    bodies = (
        _body(live.strategy_id, futures_capital_usd="600"),
        _body(second.strategy_id, futures_capital_usd="600"),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda body: live.client.post("/api/v1/deployments", json=body), bodies)
        )
    codes = sorted(response.status_code for response in responses)
    assert codes == [201, 409], [response.text for response in responses]
    refused = next(response for response in responses if response.status_code == 409)
    assert refused.json()["detail"].startswith("FUTURES_PRODUCT_OCCUPIED")
    assert len(live.execution.deployments) == 1


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"observed_at": _OPEN - timedelta(seconds=181)}, "FUTURES_COLLATERAL_UNKNOWN"),
        ({"killswitch": None}, "FUTURES_COLLATERAL_UNKNOWN"),
        ({"killswitch": True}, "FUTURES_VENUE_KILLSWITCH"),
    ],
)
def test_stale_or_unknown_venue_state_never_admits(
    live: _Live, change: dict[str, object], code: str
) -> None:
    """I8: evidence older than 180 s or an unknown/enabled killswitch refuses the start."""
    for key, value in change.items():
        setattr(live.source, key, value)
    refused = live.client.post("/api/v1/deployments", json=_body(live.strategy_id))
    assert refused.status_code == 409
    assert refused.json()["detail"].startswith(code)
    assert live.execution.deployments == {}


def test_a_stopped_books_leftover_orders_are_external(live: _Live) -> None:
    """Only running or paused live futures books' orders count as ours at a new start."""
    started = live.client.post("/api/v1/deployments", json=_body(live.strategy_id))
    assert started.status_code == 201, started.text
    book = live.execution.deployments[UUID(started.json()["id"])]
    order = Order(
        id=uuid7(_OPEN),
        deployment_id=book.id,
        intent_id=uuid7(_OPEN),
        client_order_id="managed-client",
        side=OrderSide.SELL,
        kind=OrderKind.STOP_LIMIT,
        quantity=_SIZE,
        price=Decimal(90),
        status=OrderStatus.OPEN,
        created_at=_OPEN,
        updated_at=_OPEN,
        product_id=_PERP,
    )
    asyncio.run(live.execution.save_order(order))
    second = _strategy().model_copy(update={"strategy_id": uuid7(_OPEN), "name": "Next perp"})
    asyncio.run(create_strategy_from_definition(live.strategies, second))
    live.client.post("/api/v1/deployments", json=_body(second.strategy_id))
    assert (_PERP, "managed-client") in live.source.managed
    asyncio.run(
        live.execution.save_deployment(
            replace(live.execution.deployments[book.id], status=DeploymentStatus.STOPPED)
        )
    )
    live.client.post("/api/v1/deployments", json=_body(second.strategy_id))
    assert (_PERP, "managed-client") not in live.source.managed


def test_an_unacknowledged_live_futures_start_does_no_start_work(
    live: _Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The futures 428 comes before the warmup re-save and the snapshot."""
    calls: list[object] = []

    async def adopt(store: object, strategy_id: object) -> bool:
        del store
        calls.append(strategy_id)
        return False

    monkeypatch.setattr(deployments, "adopt_warmup_policy_for_start", adopt)
    refused = live.client.post(
        "/api/v1/deployments", json=_body(live.strategy_id, i_understand_live_futures=None)
    )
    assert refused.status_code == 428
    assert refused.json()["detail"].startswith("LIVE_FUTURES_ACK_REQUIRED")
    assert calls == []


@pytest.mark.anyio
async def test_a_stale_hourly_price_leaves_the_start_margin_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A preview marked stale, or a close older than two hours, is no price at all."""
    monkeypatch.setattr(futures_live_start, "utc_now", lambda: _OPEN)
    candle = Candle(
        starts_at=_OPEN - timedelta(hours=1),
        open=Decimal(100),
        high=Decimal(101),
        low=Decimal(99),
        close=Decimal(100),
        volume=Decimal(1),
    )

    class _Preview:
        def __init__(self, *, stale: bool, latest: datetime) -> None:
            self.quality = SimpleNamespace(
                candles=(candle,), is_stale=stale, latest_completed_at=latest
            )

    class _Market:
        def __init__(self, preview: _Preview) -> None:
            self.preview = preview

        async def get_hourly_preview(self, product_id: str) -> _Preview:
            del product_id
            return self.preview

    fresh = _Market(_Preview(stale=False, latest=_OPEN))
    stale = _Market(_Preview(stale=True, latest=_OPEN))
    old = _Market(_Preview(stale=False, latest=_OPEN - timedelta(hours=3)))
    assert await futures_live_start._latest_close(
        cast("MarketDataService", fresh), _PERP
    ) == Decimal(100)
    assert await futures_live_start._latest_close(cast("MarketDataService", stale), _PERP) is None
    assert await futures_live_start._latest_close(cast("MarketDataService", old), _PERP) is None
