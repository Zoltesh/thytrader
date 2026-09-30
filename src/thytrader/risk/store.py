"""Contracts for loading and publishing the active risk policy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from thytrader.risk.models import (
    ActiveRiskPolicy,
    RiskPolicyDefinition,
    RiskPolicySource,
    compiled_default_active_policy,
    risk_policy_fingerprint,
)

if TYPE_CHECKING:
    from uuid import UUID


class RiskPolicyStoreError(RuntimeError):
    """Signal that durable risk-policy storage cannot complete a mutation."""


@runtime_checkable
class RiskPolicyStore(Protocol):
    """Load the effective policy and publish immutable replacements."""

    async def load_active(self) -> ActiveRiskPolicy:
        """Return the published active policy or the compiled default."""
        ...

    async def publish(self, definition: RiskPolicyDefinition) -> ActiveRiskPolicy:
        """Persist one new immutable version and point the active pointer at it."""
        ...


class DisabledRiskPolicyStore:
    """Use the compiled default and refuse publication without PostgreSQL."""

    async def load_active(self) -> ActiveRiskPolicy:
        """Return the compiled multi-asset envelope."""
        return compiled_default_active_policy()

    async def publish(self, definition: RiskPolicyDefinition) -> ActiveRiskPolicy:
        """Refuse publication when durable storage is unconfigured."""
        del definition
        raise RiskPolicyStoreError("Risk-policy publication requires durable storage.")


class InMemoryRiskPolicyStore:
    """Retain published policies in process memory for tests."""

    def __init__(self) -> None:
        """Start from the compiled default."""
        self._active = compiled_default_active_policy()
        self._versions: dict[str, RiskPolicyDefinition] = {
            self._active.policy_fingerprint: self._active.definition
        }

    async def load_active(self) -> ActiveRiskPolicy:
        """Return the current in-memory policy."""
        return self._active

    async def publish(self, definition: RiskPolicyDefinition) -> ActiveRiskPolicy:
        """Replace the active policy with one new immutable version."""
        fingerprint = risk_policy_fingerprint(definition)
        self._versions[fingerprint] = definition
        self._active = ActiveRiskPolicy(
            definition=definition,
            policy_fingerprint=fingerprint,
            source=RiskPolicySource.PUBLISHED,
        )
        return self._active


async def load_effective_policy(store: RiskPolicyStore | None) -> ActiveRiskPolicy:
    """Load from a store when present; otherwise use the compiled default."""
    if store is None:
        return compiled_default_active_policy()
    return await store.load_active()


def next_policy_version(current: ActiveRiskPolicy) -> int:
    """Increment the published version, or start at 1 from the compiled default."""
    if current.source is RiskPolicySource.COMPILED_DEFAULT:
        return 1
    return current.definition.version + 1


def successor_without_allocation(
    current: ActiveRiskPolicy, strategy_id: UUID
) -> RiskPolicyDefinition | None:
    """Return the next policy version minus ``strategy_id``'s allocation, if it has one.

    Deleting a strategy must not leave capital reserved for an identity that no
    longer exists (ADR 0082). ``None`` means nothing needs publishing.
    """
    remaining = tuple(
        item for item in current.definition.allocations if item.strategy_id != strategy_id
    )
    if len(remaining) == len(current.definition.allocations):
        return None
    return RiskPolicyDefinition.model_validate(
        {
            **current.definition.model_dump(mode="python"),
            "version": next_policy_version(current),
            "allocations": remaining,
        }
    )
