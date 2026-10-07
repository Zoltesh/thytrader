"""Hermetic HTTP regressions for reviewed execution-evidence correctness."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4, uuid7

from fastapi.testclient import TestClient
import pytest

from tests.execution.test_execution_quality import _deployment
from tests.execution.test_execution_quality_regressions import START, _book
from thytrader.api.app import create_app
from thytrader.api.dependencies import get_strategy_snapshot_store
from thytrader.config import Settings
from thytrader.execution.decision_store import InMemoryDecisionJournalStore
from thytrader.execution.decisions import BarDecision, DecisionOutcome
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import DeploymentMode, DeploymentSnapshot, OrderKind
from thytrader.execution.twins import DeploymentTwinLink
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.strategies.snapshots import StrategySnapshot, StrategySnapshotError

HOUR = timedelta(hours=1)


class _PinnedSnapshots:
    """Only return pinned content; a read must never record a new snapshot."""

    def __init__(self, snapshots: tuple[StrategySnapshot, ...]) -> None:
        """Hold a hermetic set of immutable proofs and record which were read."""
        self.snapshots = {s.strategy_fingerprint: s for s in snapshots}
        self.loaded: list[str] = []

    async def load(self, fingerprint: str) -> StrategySnapshot:
        """Load the exact deployment-bound proof, with no mutable-root fallback."""
        self.loaded.append(fingerprint)
        if fingerprint not in self.snapshots:
            raise StrategySnapshotError("Snapshot is unavailable.")
        return self.snapshots[fingerprint]

    async def record_snapshot(self, definition: StrategyDefinition) -> StrategySnapshot:
        """Refuse writes; evidence GET handlers are read-only."""
        del definition
        raise AssertionError("Evidence must never write a snapshot.")


def _stores(
    paper: DeploymentSnapshot,
    live: DeploymentSnapshot,
) -> tuple[InMemoryExecutionStore, InMemoryDecisionJournalStore]:
    """Seed isolated fixtures including an explicit link; never touch a runtime API."""
    execution = InMemoryExecutionStore()
    journal = InMemoryDecisionJournalStore()

    async def seed() -> None:
        """Record test-only facts through the in-memory persistence contract."""
        for snapshot in (paper, live):
            await execution.create_deployment(snapshot.deployment)
            for intent in snapshot.intents:
                await execution.save_intent(intent)
            for order in snapshot.orders:
                await execution.save_order(order)
            for fill in snapshot.fills:
                await execution.save_fill(fill)
            for intent in snapshot.intents:
                await journal.upsert(
                    BarDecision(
                        deployment_id=snapshot.deployment.id,
                        product_id=snapshot.deployment.product_id,
                        timeframe="1h",
                        mode=snapshot.deployment.mode,
                        bar_starts_at=intent.candle_starts_at,
                        bar_closes_at=intent.candle_starts_at + HOUR,
                        evaluated_at=intent.candle_starts_at + HOUR,
                        close_price="100",
                        outcome=DecisionOutcome.NO_SIGNAL,
                        reason_code="NO_SIGNAL",
                        summary="Hermetic completed bar.",
                    )
                )
        # Simulate an existing saved link; GET must independently reverify its rules.
        execution.twin_links[paper.deployment.id] = DeploymentTwinLink(
            paper.deployment.id,
            live.deployment.id,
            START,
        )

    asyncio.run(seed())
    return execution, journal


@pytest.mark.parametrize("proof_kind", ["equivalent", "incompatible", "unavailable"])
def test_twin_get_reverifies_pinned_rule_equivalence(proof_kind: str) -> None:
    """A saved link is not a rule proof; unavailable or incompatible clones cannot compare."""
    definition = create_template_strategy(product_id="BTC-USD")
    clone = definition.model_copy(update={"strategy_id": uuid7(), "name": "Clone"})
    if proof_kind == "incompatible":
        clone = clone.model_copy(
            update={
                "execution": clone.execution.model_copy(
                    update={
                        "max_entry_wait_bars": clone.execution.max_entry_wait_bars + 1,
                    }
                )
            }
        )
    proofs = (
        StrategySnapshot(strategy_fingerprint(definition), definition),
        StrategySnapshot(strategy_fingerprint(clone), clone),
    )
    paper, _ = _book(
        replace(_deployment(uuid4()), strategy_fingerprint=proofs[0].strategy_fingerprint)
    )
    live, _ = _book(
        replace(
            _deployment(uuid4(), mode=DeploymentMode.LIVE),
            strategy_fingerprint=proofs[1].strategy_fingerprint,
        )
    )
    execution, journal = _stores(paper, live)
    pinned = _PinnedSnapshots(() if proof_kind == "unavailable" else proofs)
    app = create_app(
        Settings(_env_file=None), execution_store=execution, decision_journal_store=journal
    )

    def snapshot_reader() -> _PinnedSnapshots:
        """Override only the read-only snapshot dependency."""
        return pinned

    app.dependency_overrides[get_strategy_snapshot_store] = snapshot_reader
    with TestClient(app) as client:
        response = client.get(f"/api/v1/deployments/{paper.deployment.id}/execution-quality/twin")
    assert response.status_code == 200
    body = response.json()
    assert body["comparable"] is (proof_kind == "equivalent")
    assert body["summaries_context_only"] is (proof_kind != "equivalent")
    assert proofs[0].strategy_fingerprint in pinned.loaded
    if proof_kind != "unavailable":
        assert proofs[1].strategy_fingerprint in pinned.loaded
    if proof_kind == "incompatible":
        assert "trading_rules_incompatible" in body["reasons"]
    elif proof_kind == "unavailable":
        assert "trading_rules_unverified" in body["reasons"]
    assert execution.fills == {f.id: f for s in (paper, live) for f in s.fills}


def test_unknown_liquidity_counterfactual_is_json_null_not_zero() -> None:
    """Public nullable costs preserve the exact observed fee population."""
    paper, _ = _book(_deployment(uuid4()))
    live, _ = _book(
        _deployment(uuid4(), mode=DeploymentMode.LIVE), exit_kind=OrderKind.TRIGGER_BRACKET
    )
    execution, journal = _stores(paper, live)
    with TestClient(
        create_app(
            Settings(_env_file=None), execution_store=execution, decision_journal_store=journal
        )
    ) as client:
        response = client.get(f"/api/v1/deployments/{live.deployment.id}/execution-quality/twin")
    assert response.status_code == 200
    normalization = response.json()["fee_normalization"]
    assert normalization["observed_live_fees"] == "0.21"
    assert normalization["counterfactual_live_fees_at_paper_rates"] is None
    assert normalization["fee_delta"] is None
    assert normalization["population"] == "live_applied_fill_lifetime"
    assert normalization["fill_count"] == 2


def test_only_future_journal_closes_never_appear_as_causal_slippage() -> None:
    """The read route rejects a fill bar's final close without a completed intent reference."""
    paper, _ = _book(_deployment(uuid4()))
    live, _ = _book(_deployment(uuid4(), mode=DeploymentMode.LIVE))
    execution, _ = _stores(paper, live)
    journal = InMemoryDecisionJournalStore()

    async def future_rows() -> None:
        """Only the bar starting at submission/fill time is journaled."""
        for fill in live.fills:
            await journal.upsert(
                BarDecision(
                    deployment_id=live.deployment.id,
                    product_id="BTC-USD",
                    timeframe="1h",
                    mode=DeploymentMode.LIVE,
                    bar_starts_at=fill.filled_at,
                    bar_closes_at=fill.filled_at + HOUR,
                    evaluated_at=fill.filled_at + HOUR,
                    close_price="200",
                    outcome=DecisionOutcome.NO_SIGNAL,
                    reason_code="NO_SIGNAL",
                    summary="A future bar close.",
                )
            )

    asyncio.run(future_rows())
    with TestClient(
        create_app(
            Settings(_env_file=None), execution_store=execution, decision_journal_store=journal
        )
    ) as client:
        response = client.get(f"/api/v1/deployments/{live.deployment.id}/execution-quality")
    assert response.status_code == 200
    entry = response.json()["books"][0]["recorded_fills"][0]
    assert entry["slippage_bps"] is None
    assert entry["reference_price"] is None
    assert "fill_without_journaled_close" in response.json()["evidence"]["reasons"]
