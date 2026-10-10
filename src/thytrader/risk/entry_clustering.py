"""Fleet entry clustering cap (ADR 0125).

Bounds how many books open new risk around one event, such as every 2h/4h/6h/1d bar
closing at 00:00 UTC. The count is the distinct ``(deployment, product)`` pairs with an
``ENTRY``-purpose intent created in the trailing window, across every book of the entry's
mode, stopped books included. ``ADOPTION`` intents are not entries. A reprice of an already
admitted working entry and an in-kind adoption are not gated. Protective exits never reach
this module: only the entry gate calls it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from thytrader.risk.gate_common import _deny
from thytrader.risk.models import RiskReasonCode
from thytrader.trading.models import IntentPurpose, resolved_product_id

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

    from thytrader.risk.breakers import EntryObservation
    from thytrader.risk.gate_common import ProposedEntry
    from thytrader.risk.models import RiskPolicyDefinition, RiskVerdict
    from thytrader.trading.models import DeploymentMode, DeploymentSnapshot


def cluster_verdict(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    proposed: ProposedEntry,
    snapshots: Sequence[DeploymentSnapshot],
    observation: EntryObservation | None,
) -> RiskVerdict | None:
    """Deny when the fleet's recent distinct entries already reach the policy cap.

    Returns ``None`` (no objection) when the cap is unset, for an in-kind adoption, and for
    a reprice of an admitted working entry. Without an observation there is no ``as_of``
    to anchor the window, so a set cap denies.
    """
    limit = policy.max_fleet_entries_per_window
    minutes = policy.fleet_entry_window_minutes
    if limit is None or minutes is None:
        return None
    if proposed.in_kind or proposed.readmits_working_entry:
        return None
    if observation is None:
        return _deny(
            RiskReasonCode.FLEET_ENTRY_CLUSTER_LIMIT,
            "Fleet entry cluster limit cannot be evaluated without an observation time; "
            "new entries are blocked.",
        )
    window = timedelta(minutes=minutes)
    latest = _latest_entry_per_book_product(snapshots, mode=mode, since=observation.as_of - window)
    if len(latest) < limit:
        return None
    ordered = sorted(latest.values())
    frees_at = ordered[len(ordered) - limit] + window
    return _deny(
        RiskReasonCode.FLEET_ENTRY_CLUSTER_LIMIT,
        f"Fleet entry cluster limit: {len(ordered)} new entries in the last {minutes} minutes, "
        f"cap={limit}; oldest at {ordered[0].isoformat()}, a slot frees at "
        f"{frees_at.isoformat()}.",
    )


def _latest_entry_per_book_product(
    snapshots: Sequence[DeploymentSnapshot], *, mode: DeploymentMode, since: datetime
) -> dict[tuple[UUID, str], datetime]:
    """Map each ``(deployment, product)`` with an ENTRY intent since ``since`` to its newest.

    A pair stays counted until its newest in-window entry ages out, which is when it frees
    its slot. Blank intent products resolve to the book's primary product.
    """
    latest: dict[tuple[UUID, str], datetime] = {}
    for snapshot in snapshots:
        deployment = snapshot.deployment
        if deployment.mode is not mode:
            continue
        for intent in snapshot.intents:
            if intent.purpose is not IntentPurpose.ENTRY or intent.created_at < since:
                continue
            key = (deployment.id, resolved_product_id(intent.product_id, deployment))
            prior = latest.get(key)
            if prior is None or intent.created_at > prior:
                latest[key] = intent.created_at
    return latest
