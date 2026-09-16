"""PostgreSQL repository for immutable risk-policy versions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.persistence.schema import active_risk_policy, published_risk_policies
from thytrader.risk.models import (
    ActiveRiskPolicy,
    RiskPolicyDefinition,
    RiskPolicySource,
    canonical_risk_policy_bytes,
    compiled_default_active_policy,
    definition_from_stored_json,
    risk_policy_fingerprint,
    stored_canonical_fingerprint,
)
from thytrader.risk.store import RiskPolicyStoreError

if TYPE_CHECKING:
    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncEngine


class PostgresRiskPolicyStore:
    """Persist published risk policies and a single active pointer."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind the store to a managed async engine."""
        self._engine = engine

    async def load_active(self) -> ActiveRiskPolicy:
        """Return the active published policy, or the compiled default when unset."""
        statement = (
            select(
                published_risk_policies.c.policy_fingerprint,
                published_risk_policies.c.canonical_definition,
            )
            .join(
                active_risk_policy,
                active_risk_policy.c.policy_fingerprint
                == published_risk_policies.c.policy_fingerprint,
            )
            .where(active_risk_policy.c.id == 1)
        )
        try:
            async with self._engine.connect() as connection:
                row = (await connection.execute(statement)).mappings().one_or_none()
        except SQLAlchemyError as error:
            raise RiskPolicyStoreError("Risk-policy storage is unavailable.") from error
        if row is None:
            return compiled_default_active_policy()
        return _published_from_row(row)

    async def publish(self, definition: RiskPolicyDefinition) -> ActiveRiskPolicy:
        """Insert one immutable version and point the singleton active row at it."""
        fingerprint = risk_policy_fingerprint(definition)
        canonical = canonical_text(definition)
        now = datetime.now(UTC)
        insert_policy = (
            insert(published_risk_policies)
            .values(
                policy_fingerprint=fingerprint,
                policy_id=str(definition.policy_id),
                version=definition.version,
                canonical_definition=canonical,
                published_at=now,
            )
            .on_conflict_do_nothing()
        )
        upsert_active = (
            insert(active_risk_policy)
            .values(id=1, policy_fingerprint=fingerprint, activated_at=now)
            .on_conflict_do_update(
                index_elements=["id"],
                set_={"policy_fingerprint": fingerprint, "activated_at": now},
            )
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(insert_policy)
                await connection.execute(upsert_active)
        except SQLAlchemyError as error:
            raise RiskPolicyStoreError("Risk-policy storage is unavailable.") from error
        return ActiveRiskPolicy(
            definition=definition,
            policy_fingerprint=fingerprint,
            source=RiskPolicySource.PUBLISHED,
        )


def canonical_text(definition: RiskPolicyDefinition) -> str:
    """Render canonical policy bytes as UTF-8 text for storage."""
    return canonical_risk_policy_bytes(definition).decode("utf-8")


def _published_from_row(row: RowMapping) -> ActiveRiskPolicy:
    """Revalidate stored canonical JSON before treating it as the active policy."""
    raw = str(row["canonical_definition"])
    fingerprint = str(row["policy_fingerprint"])
    if fingerprint != stored_canonical_fingerprint(raw):
        raise RiskPolicyStoreError("Stored risk-policy fingerprint does not match canonical bytes.")
    try:
        definition = definition_from_stored_json(raw)
    except (ValueError, ValidationError) as error:
        raise RiskPolicyStoreError("Stored risk policy failed revalidation.") from error
    return ActiveRiskPolicy(
        definition=definition,
        policy_fingerprint=fingerprint,
        source=RiskPolicySource.PUBLISHED,
    )
