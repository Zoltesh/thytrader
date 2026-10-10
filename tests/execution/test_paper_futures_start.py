"""Starting a paper futures deployment binds its contract once (ADR 0129 §4)."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from tests.api.test_deployments import _client, _published_strategy
from tests.execution.test_htf_filter import _Catalog
from tests.execution.test_paper_futures_books import _START, _observation, _policy, _strategy
from tests.strategy_fakes import SeededStrategyStore as InMemoryPublicationStore
from thytrader.execution.futures_start import FuturesStart
from thytrader.execution.service import create_deployment
from thytrader.market_data.instruments import InstrumentKind
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.strategies.models import strategy_fingerprint
from thytrader.strategies.snapshots import StrategySnapshot
from thytrader.trading.futures_book import InMemoryFuturesContractStore
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode, ExecutionConflictError

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.market_data.futures_observations import (
        FundingRateRecord,
        FuturesInstrumentObservation,
    )
    from thytrader.trading.models import Deployment


class _Latest:
    """One recorded observation, or none."""

    def __init__(self, observation: FuturesInstrumentObservation | None) -> None:
        self.observation = observation

    async def latest_instrument(
        self, product_id: str
    ) -> tuple[FuturesInstrumentObservation, datetime] | None:
        """Return the bound observation."""
        del product_id
        return None if self.observation is None else (self.observation, _START)

    async def funding_rates(
        self, *, product_id: str | None, starts_at: datetime, ends_at: datetime
    ) -> tuple[FundingRateRecord, ...]:
        """No funding is read at start."""
        del product_id, starts_at, ends_at
        return ()


async def _start(
    *,
    observation: FuturesInstrumentObservation | None = None,
    contracts: InMemoryFuturesContractStore | None = None,
    **overrides: Any,
) -> tuple[Deployment, InMemoryFuturesContractStore, InMemoryExecutionStore]:
    """Start the perp strategy in paper with explicit fees unless overridden."""
    strategy = _strategy()
    store = InMemoryExecutionStore()
    risk_store = InMemoryRiskPolicyStore()
    await risk_store.publish(_policy())
    bindings = contracts or InMemoryFuturesContractStore()
    arguments: dict[str, Any] = {
        "paper_maker_fee_rate": Decimal(0),
        "paper_taker_fee_rate": Decimal("0.0005"),
        "paper_fee_per_contract": Decimal("0.15"),
    }
    arguments.update(overrides)
    created = await create_deployment(
        store=store,
        publication_store=_Catalog(strategy),
        strategy_fingerprint=strategy_fingerprint(strategy),
        mode=DeploymentMode.PAPER,
        paper_starting_cash=Decimal(10000),
        live_allowed=False,
        risk_store=risk_store,
        futures_start=FuturesStart(
            contracts=bindings,
            observations=_Latest(observation or _observation()),
        ),
        **arguments,
    )
    return created, bindings, store


@pytest.mark.anyio
async def test_a_paper_futures_start_binds_the_observed_contract_and_fee() -> None:
    """The binding is the recorded observation's contract, written once."""
    created, bindings, _store = await _start()
    binding = await bindings.load_contract(created.id)
    assert binding is not None
    assert binding.contract.product_id == "BIP-20DEC30-CDE"
    assert binding.contract.contract_size == "0.01"
    assert binding.contract.expires_at is None
    assert binding.contract.catalog_fingerprint == _observation().payload_fingerprint
    assert binding.fee_per_contract == Decimal("0.15")
    assert created.paper_taker_fee_rate == Decimal("0.0005")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"paper_fee_per_contract": None}, "FUTURES_FEE_REQUIRED"),
        ({"paper_maker_fee_rate": None, "paper_taker_fee_rate": None}, "FUTURES_FEE_REQUIRED"),
    ],
)
async def test_a_paper_futures_start_names_its_own_fees(
    overrides: dict[str, Any], message: str
) -> None:
    """The account's spot rates never stand in for futures fees."""
    with pytest.raises(ExecutionConflictError, match=message):
        await _start(**overrides)


@pytest.mark.anyio
async def test_dated_and_unobserved_contracts_are_refused() -> None:
    """Paper books trade perp-style contracts the catalog has recorded."""
    dated = replace(_observation(), kind=InstrumentKind.DATED_FUTURE)
    with pytest.raises(ExecutionConflictError, match="perp-style contracts only"):
        await _start(observation=dated)
    with pytest.raises(ExecutionConflictError, match="FUTURES_UNDERLYING_MISMATCH"):
        await _start(observation=replace(_observation(), underlying="ETH"))


def test_the_api_refuses_a_per_contract_fee_on_a_spot_strategy() -> None:
    """``paper_fee_per_contract`` reaches the start and is a conflict for spot books."""
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)
    publication = InMemoryPublicationStore()
    publication.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )
    with _client(publication, InMemoryExecutionStore()) as client:
        refused = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "paper",
                "paper_starting_cash": "10000",
                "maker_fee_rate": "0.0025",
                "taker_fee_rate": "0.004",
                "paper_fee_per_contract": "0.15",
            },
        )
    assert refused.status_code == 409
    assert "futures strategies only" in refused.json()["detail"]
