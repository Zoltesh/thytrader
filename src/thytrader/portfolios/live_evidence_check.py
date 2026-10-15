"""Live-start evidence for portfolio sleeves, as for a single live bot (ADR 0143)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from thytrader.research.live_evidence import gather_live_start_evidence
from thytrader.research.live_evidence_store import LiveEvidenceStoreUnavailableError
from thytrader.trading.ids import utc_now

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.research.catalog import ResearchStudyCatalog
    from thytrader.research.live_evidence import LiveStartEvidence
    from thytrader.research.live_evidence_store import LiveEvidenceStore
    from thytrader.risk.start_safeguards import StartSafeguards
    from thytrader.strategies.snapshots import StrategySnapshot
    from thytrader.trading.store import ExecutionStore


@dataclass(frozen=True, slots=True)
class LiveEvidenceSources:
    """Where a live sleeve's evidence is read from and recorded to."""

    catalog: ResearchStudyCatalog | None
    store: LiveEvidenceStore


async def sleeve_live_evidence(
    sources: LiveEvidenceSources,
    snapshot: StrategySnapshot,
    *,
    execution: ExecutionStore,
    safeguards: StartSafeguards,
) -> LiveStartEvidence:
    """The evidence a live sleeve's snapshot has, against the policy's thresholds."""
    return await gather_live_start_evidence(
        strategy_fingerprint=snapshot.strategy_fingerprint,
        strategy_id=snapshot.definition.strategy_id,
        catalog=sources.catalog,
        execution=execution,
        thresholds=safeguards.live_evidence_thresholds,
        now=utc_now(),
    )


async def record_sleeve_evidence(
    sources: LiveEvidenceSources,
    deployment_id: UUID,
    evidence: LiveStartEvidence,
    *,
    warnings: list[str],
) -> None:
    """Keep the evidence with the started sleeve book; a storage failure only warns."""
    try:
        await sources.store.record(deployment_id, evidence)
    except LiveEvidenceStoreUnavailableError:
        warnings.append(
            "LIVE_EVIDENCE_NOT_RECORDED: the sleeve started, but its live-start evidence "
            "could not be stored."
        )
