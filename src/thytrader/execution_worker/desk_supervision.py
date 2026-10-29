"""Agent desk wake supervision of the execution worker cycle (ADR 0147, 0115).

Once per cycle the worker reads every desk and its newest wakes and raises, refreshes or
resolves the ``DESK_WAKES_FAILING``, ``DESK_WAKES_STALLED`` and ``DESK_UNREADABLE`` alerts in
the durable alert feed. Desks are woken by the operator's own timers, outside ThyTrader's
processes, so this is the only place a dead harness, a stopped timer or a desk row that no
longer reads becomes visible. It never changes a desk. The
pass runs in the cycle's timed ``fleet_supervision`` phase and gives up after
:data:`PASS_SECONDS`, treating its evidence as unknown, so a slow desk store cannot hold the
cycle's book supervision.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from thytrader.agent_desk.store import DeskStoreUnavailableError
from thytrader.alerts.supervision_desks import FAILED_WAKES, desk_wake_evidence
from thytrader.execution_worker.ports import _logger
from thytrader.trading.ids import utc_now

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime

    from thytrader.agent_desk.models import DeskWake
    from thytrader.agent_desk.store import DeskStore
    from thytrader.alerts.service import AlertService

PASS_SECONDS = 10.0


async def supervise_desks(
    *,
    alert_service: AlertService | None,
    desks: DeskStore | None,
    clock: Callable[[], datetime] | None = None,
) -> None:
    """Raise or resolve the desk wake alerts; never fail or hold the worker cycle.

    The observation is stamped as the read begins (ADR 0115: a timestamp marks the start of its
    evidence pass); a wake journaled while the read runs is then newer than the stamp and can
    only look fresh. Without ``clock`` the module's ``utc_now`` is read at call time, so a
    patched worker clock reaches this pass.
    """
    if alert_service is None or desks is None:
        return
    try:
        async with asyncio.timeout(PASS_SECONDS):
            await _supervise(alert_service, desks, clock=clock)
    except TimeoutError:
        _logger.warning("desk_supervision_timed_out seconds=%s", PASS_SECONDS)
    except Exception as error:  # noqa: BLE001 - supervision must never fail the cycle.
        _logger.warning("desk_supervision_unavailable type=%s", type(error).__name__)


async def _supervise(
    alert_service: AlertService, desks: DeskStore, *, clock: Callable[[], datetime] | None
) -> None:
    """One evidence pass: read, judge, apply, then log each newly raised alert."""
    observed_at = (utc_now if clock is None else clock)()
    listed = await desks.list_desks()
    unreadable = await desks.unreadable_desks()
    wakes: dict[str, Sequence[DeskWake] | None] = {}
    for desk in listed:
        try:
            wakes[desk.desk_id] = await desks.list_wakes(desk.desk_id, limit=FAILED_WAKES)
        except DeskStoreUnavailableError:
            wakes[desk.desk_id] = None
    previous = await alert_service.open_alerts()
    evidence = desk_wake_evidence(
        listed, wakes, now=observed_at, prior_alerts=previous, unreadable=unreadable
    )
    application = await alert_service.apply(
        evidence.findings, now=observed_at, evaluated=evidence.evaluated, dispatch=False
    )
    for change in application.changes:
        if change.created:
            _logger.warning(
                "desk_wake_alert code=%s subject=%s severity=%s",
                change.alert.code.value,
                change.alert.subject,
                change.alert.severity.value,
            )
