"""Deployment HTTP exposes qualified opening provenance separately from raw legacy equity."""

import asyncio
from dataclasses import replace
from decimal import Decimal

from tests.api.test_deployments import _client, _published_strategy
from tests.risk.test_safety_evidence import _TODAY, overnight_long, seed_accounting
from tests.strategy_fakes import SeededStrategyStore as InMemoryPublicationStore
from thytrader.risk.opening_accounting import reconstruct_day_open
from thytrader.strategies.models import strategy_fingerprint
from thytrader.strategies.snapshots import StrategySnapshot
from thytrader.trading.day_open import MidnightMark
from thytrader.trading.memory import InMemoryExecutionStore


def test_http_separates_verified_opening_from_legacy_observation() -> None:
    """Exact qualified evidence is additive; list/detail preserve raw recorded financial values."""
    publication = InMemoryPublicationStore()
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )
    full = overnight_long()
    evidence = reconstruct_day_open(
        full,
        as_of=_TODAY,
        marks=(
            MidnightMark(
                product_id="BTC-USD", closes_at=_TODAY.replace(hour=0), price=Decimal("100")
            ),
        ),
    )
    assert evidence is not None
    root = replace(
        full.deployment,
        strategy_fingerprint=fingerprint,
        strategy_id=definition.strategy_id,
        risk_day_open_evidence=evidence,
    )
    execution = InMemoryExecutionStore()
    asyncio.run(seed_accounting(execution, replace(full, deployment=root)))
    with _client(publication, execution) as client:
        detail = client.get(f"/api/v1/deployments/{root.id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["cash"] == "9900"
        assert body["capital"]["utc_day_open_equity"] == "9950"
        qualified = body["capital"]["risk_day_open_evidence"]
        assert qualified["source"] == "per_product_applied_fills_v1"
        assert qualified["equity"] == "10000"
        assert qualified["marks"][0]["price"] == "100"
        listing = client.get("/api/v1/deployments")
        assert listing.status_code == 200
        assert listing.json()["deployments"][0]["capital"]["risk_day_open_evidence"] == qualified
