"""Paper starts read the account's Coinbase rates, or are refused with HTTP 409."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, cast

import pytest

from tests.api.test_deployments import _client, _published_strategy
from tests.strategy_fakes import SeededStrategyStore as InMemoryPublicationStore
from thytrader.api import paper_fees
from thytrader.api.paper_fees import account_paper_fee_source as _account_source
from thytrader.exchanges.fees import FeeProfile
from thytrader.execution.paper_fees import PaperFeesUnavailableError
from thytrader.strategies.models import strategy_fingerprint
from thytrader.strategies.snapshots import StrategySnapshot
from thytrader.trading.memory import InMemoryExecutionStore

if TYPE_CHECKING:
    from thytrader.portfolio.service import PortfolioService

_PROFILE = FeeProfile(
    taker_fee_rate=Decimal("0.009"),
    maker_fee_rate=Decimal("0.005"),
    usd_volume_30d=Decimal("120"),
    fee_tier="Intro",
    as_of=datetime(2026, 10, 7, 5, tzinfo=UTC),
)


class _Service:
    """The two members of ``PortfolioService`` the fee source reads."""

    def __init__(self, *, demo: bool = False, fails: bool = False) -> None:
        self.demo = demo
        self.fails = fails

    async def get_fee_profile(self) -> FeeProfile:
        if self.fails:
            raise RuntimeError("provider detail that must not leak")
        return _PROFILE


def _source(service: _Service) -> paper_fees.PaperFeeSource:
    return _account_source(cast("PortfolioService", service))


@pytest.mark.anyio
async def test_the_account_source_returns_the_accounts_reported_rates() -> None:
    """The rates are the account's own maker/taker, as ``GET /api/v1/fees`` suggests."""
    assert await _source(_Service())() == (Decimal("0.005"), Decimal("0.009"))


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("service", "reason"),
    [
        (_Service(demo=True), "demo or missing credentials"),
        (_Service(fails=True), "could not be read"),
    ],
)
async def test_the_account_source_refuses_without_account_rates(
    service: _Service, reason: str
) -> None:
    """Demo and failed reads are refusals with a redacted reason, never schedule guesses."""
    with pytest.raises(PaperFeesUnavailableError, match=reason) as raised:
        await _source(service)()
    assert "provider detail" not in str(raised.value)


def test_a_paper_start_is_refused_with_409_when_the_account_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without Coinbase credentials a paper start needs explicit rates; nothing is created."""
    monkeypatch.setattr(paper_fees, "account_paper_fee_source", _account_source)
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)
    with _client(publication, execution) as client:
        publication.published[fingerprint] = StrategySnapshot(
            strategy_fingerprint=fingerprint, definition=definition
        )
        body = {
            "strategy_id": str(definition.strategy_id),
            "mode": "paper",
            "paper_starting_cash": "10000",
        }
        refused = client.post("/api/v1/deployments", json=body)
        listed = client.get("/api/v1/deployments").json()
        explicit = client.post(
            "/api/v1/deployments",
            json={**body, "maker_fee_rate": "0.004", "taker_fee_rate": "0.006"},
        )
    assert refused.status_code == 409
    assert "--maker-fee-rate" in refused.json()["detail"]
    assert listed["deployments"] == []
    assert explicit.status_code == 201
    assert (explicit.json()["maker_fee_rate"], explicit.json()["taker_fee_rate"]) == (
        "0.004",
        "0.006",
    )
