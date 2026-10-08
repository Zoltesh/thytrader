"""Bounded decision-journal paging for execution-quality slippage references (ADR 0116).

Reads, newest first and with a page cap per product, the journaled decision closes
that the applied fills' original intent bars need. A missing journal yields no closes
and no coverage, never a default.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.decision_store import DecisionJournalStore, decision_storage_label
from thytrader.execution.decisions import DECISION_PAGE_MAX_LIMIT
from thytrader.execution.execution_quality_models import (
    JournaledCloseEvidence,
    JournaledDecisionClose,
)
from thytrader.trading.models import DeploymentSnapshot, resolved_product_id

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime
    from uuid import UUID

    from thytrader.trading.models import Deployment, Order


_DECISION_FETCH_MAX_PAGES = 25


JOURNALED_CLOSE_PAGE_LIMIT = DECISION_PAGE_MAX_LIMIT


async def load_journaled_close_evidence(
    store: DecisionJournalStore,
    *,
    deployment_id: UUID,
    snapshot: DeploymentSnapshot,
    page_limit: int = JOURNALED_CLOSE_PAGE_LIMIT,
    max_pages: int = _DECISION_FETCH_MAX_PAGES,
) -> JournaledCloseEvidence:
    """Page the decision journal for the closes needed by one snapshot's fills.

    Paging is bounded: at most ``max_pages`` newest-first pages per product are read,
    stopping early once rows reach the earliest applied fill's original intent bar.
    A missing journal yields no closes and no coverage, never a default.
    """
    if decision_storage_label(store) == "unavailable":
        return JournaledCloseEvidence({}, None, 0, False)
    deployment = snapshot.deployment
    orders = {order.id: order for order in snapshot.orders}
    earliest = _earliest_fill_bars(snapshot, deployment, orders)
    products = sorted(earliest)
    closes: dict[tuple[str, datetime], JournaledDecisionClose] = {}
    rows = 0
    limited = False
    for product_id in products:
        cursor: str | None = None
        exhausted = False
        for _page in range(max_pages):
            page = await store.list_for_deployment(
                deployment_id, limit=page_limit, cursor=cursor, product_id=product_id
            )
            for decision in page.decisions:
                rows += 1
                if decision.close_price is not None:
                    closes[(product_id, decision.bar_starts_at)] = JournaledDecisionClose(
                        price=Decimal(decision.close_price), bar_closes_at=decision.bar_closes_at
                    )
            cursor = page.next_cursor
            if cursor is None:
                exhausted = True
                break
            oldest_seen = page.decisions[-1].bar_starts_at if page.decisions else None
            if oldest_seen is not None and oldest_seen <= earliest[product_id]:
                exhausted = True  # All required intent bars were reached, not cap-limited.
                break
        if not exhausted:
            limited = True
    coverage = None
    if closes:
        bars = [bar for _product, bar in closes]
        coverage = (min(bars), max(bars))
    return JournaledCloseEvidence(closes, coverage, rows, limited)


def _earliest_fill_bars(
    snapshot: DeploymentSnapshot,
    deployment: Deployment,
    orders: Mapping[UUID, Order],
) -> dict[str, datetime]:
    """Find the earliest decision bar needed by applied fills, not their later fill bars."""
    earliest: dict[str, datetime] = {}
    intents = {intent.id: intent for intent in snapshot.intents}
    for fill in snapshot.fills:
        order = orders.get(fill.order_id)
        if order is None or fill.economics_applied_at is None:
            continue
        product_id = resolved_product_id(order.product_id, deployment)
        intent = intents.get(order.intent_id)
        if intent is None:
            continue
        bar = intent.candle_starts_at
        current = earliest.get(product_id)
        if current is None or bar < current:
            earliest[product_id] = bar
    return earliest
