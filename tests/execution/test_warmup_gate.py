"""Paper and live starts and resumes require the convergence warmup policy (ADR 0140)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
import json

import pytest

from tests.execution.decision_support import Catalog
from thytrader.execution.service import create_deployment, set_deployment_status
from thytrader.execution.warmup_gate import WARMUP_POLICY_REQUIRED, convergence_policy_message
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.models import (
    StrategyDefinition,
    canonical_strategy_bytes,
    strategy_fingerprint,
)
from thytrader.strategies.warmup_policy import with_convergence_warmup, without_warmup_policy
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    ExecutionConflictError,
    ExecutionStoreError,
)

pytestmark = pytest.mark.anyio

_NOW = datetime(2026, 10, 1, tzinfo=UTC)


def _plain() -> StrategyDefinition:
    """A strategy saved before the policy: the default template without it."""
    document = json.loads(canonical_strategy_bytes(create_template_strategy(now=_NOW)))
    return StrategyDefinition.model_validate(without_warmup_policy(document))


async def _start(definition: StrategyDefinition, store: InMemoryExecutionStore) -> None:
    await create_deployment(
        store=store,
        publication_store=Catalog(definition),
        strategy_fingerprint=strategy_fingerprint(definition),
        mode=DeploymentMode.PAPER,
        paper_starting_cash=Decimal("10000"),
        live_allowed=False,
    )


async def test_a_snapshot_without_the_policy_cannot_start() -> None:
    """The refusal names the reason code and the re-save that adopts the policy."""
    definition = _plain()
    assert definition.data_requirements.warmup_policy is None
    store = InMemoryExecutionStore()
    with pytest.raises(ExecutionConflictError) as caught:
        await _start(definition, store)
    message = str(caught.value)
    assert message.startswith(f"{WARMUP_POLICY_REQUIRED}: ")
    assert "Nothing was started" in message
    assert f"show-strategy --strategy-id {definition.strategy_id}" in message
    assert await store.list_deployments() == ()


async def test_a_converged_snapshot_starts() -> None:
    """The same strategy under the policy starts normally."""
    definition = with_convergence_warmup(_plain())
    store = InMemoryExecutionStore()
    await _start(definition, store)
    (deployment,) = await store.list_deployments()
    assert deployment.strategy_fingerprint == strategy_fingerprint(definition)


async def test_resuming_a_book_without_the_policy_is_refused() -> None:
    """Pausing still works; resuming a pre-policy book is refused and it stays paused."""
    converged = with_convergence_warmup(_plain())
    store = InMemoryExecutionStore()
    await _start(converged, store)
    (deployment,) = await store.list_deployments()
    legacy = _plain()
    # A book started before the gate existed runs a pre-policy snapshot.
    await store.save_deployment(
        replace(deployment, strategy_fingerprint=strategy_fingerprint(legacy))
    )
    catalog = Catalog(legacy)
    paused = await set_deployment_status(
        store=store,
        deployment_id=deployment.id,
        status=DeploymentStatus.PAUSED,
        publication_store=catalog,
    )
    assert paused.deployment.status is DeploymentStatus.PAUSED
    with pytest.raises(ExecutionConflictError) as caught:
        await set_deployment_status(
            store=store,
            deployment_id=deployment.id,
            status=DeploymentStatus.RUNNING,
            publication_store=catalog,
        )
    message = str(caught.value)
    assert message.startswith(f"{WARMUP_POLICY_REQUIRED}: ")
    assert "Nothing was resumed" in message
    assert "as a new bot after stopping this one" in message
    current = await store.get_deployment(deployment.id)
    assert current.deployment.status is DeploymentStatus.PAUSED


async def test_resuming_a_converged_book_works() -> None:
    """A book running a converged snapshot pauses and resumes as before."""
    definition = with_convergence_warmup(_plain())
    store = InMemoryExecutionStore()
    await _start(definition, store)
    (deployment,) = await store.list_deployments()
    catalog = Catalog(definition)
    await set_deployment_status(
        store=store,
        deployment_id=deployment.id,
        status=DeploymentStatus.PAUSED,
        publication_store=catalog,
    )
    resumed = await set_deployment_status(
        store=store,
        deployment_id=deployment.id,
        status=DeploymentStatus.RUNNING,
        publication_store=catalog,
    )
    assert resumed.deployment.status is DeploymentStatus.RUNNING


async def test_discretionary_books_resume_without_a_strategy_snapshot() -> None:
    """A discretionary book evaluates no strategy rules, so no snapshot is loaded."""
    definition = with_convergence_warmup(_plain())
    store = InMemoryExecutionStore()
    await _start(definition, store)
    (deployment,) = await store.list_deployments()
    await store.save_deployment(
        replace(
            deployment,
            kind=DeploymentKind.DISCRETIONARY,
            status=DeploymentStatus.PAUSED,
            strategy_fingerprint="sha256:" + "0" * 64,
        )
    )
    resumed = await set_deployment_status(
        store=store,
        deployment_id=deployment.id,
        status=DeploymentStatus.RUNNING,
        publication_store=Catalog(definition),
    )
    assert resumed.deployment.status is DeploymentStatus.RUNNING


def test_an_unreachable_convergence_warmup_says_to_shorten_the_indicator() -> None:
    """Re-saving cannot help a strategy whose convergence exceeds the declarable warmup."""
    document = json.loads(canonical_strategy_bytes(_plain()))
    document["indicators"].append(
        {
            "id": "slow_kama",
            "kind": "kama",
            "input": "close",
            "parameters": {"period": 10, "fast_period": 2, "slow_period": 500},
        }
    )
    definition = StrategyDefinition.model_validate(document)
    message = convergence_policy_message(definition, action="started")
    assert "more than the 10000 a strategy can declare" in message
    assert "Shorten its slowest indicator" in message
    assert "save-strategy" not in message
    resumed = convergence_policy_message(definition, action="resumed")
    assert resumed.endswith("then start it as a new bot after stopping this one.")


async def test_a_resume_whose_snapshot_cannot_load_fails_closed() -> None:
    """Without its snapshot the policy cannot be confirmed, so the book stays paused."""
    definition = with_convergence_warmup(_plain())
    store = InMemoryExecutionStore()
    await _start(definition, store)
    (deployment,) = await store.list_deployments()
    await store.save_deployment(
        replace(
            deployment,
            status=DeploymentStatus.PAUSED,
            strategy_fingerprint="sha256:" + "0" * 64,
        )
    )
    with pytest.raises(ExecutionStoreError, match="not found"):
        await set_deployment_status(
            store=store,
            deployment_id=deployment.id,
            status=DeploymentStatus.RUNNING,
            publication_store=Catalog(definition),
        )
    current = await store.get_deployment(deployment.id)
    assert current.deployment.status is DeploymentStatus.PAUSED
