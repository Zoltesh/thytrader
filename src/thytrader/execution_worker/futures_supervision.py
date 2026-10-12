"""Isolated read-only futures safety observer using the existing alert delivery path."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from thytrader.alerts.models import AlertCode, AlertScope, AlertSeverity, SupervisionFinding
from thytrader.alerts.supervision_futures import futures_safety_evidence, orphan_finding
from thytrader.execution.audit_scope import bound_execution_audit_store
from thytrader.execution.futures_live import (
    current_live_futures_runtime,
    prepare_live_futures_book,
)
from thytrader.execution.futures_paper import current_futures_runtime
from thytrader.execution.futures_visibility import mirror_live_account, visibility_scope
from thytrader.execution_worker.ports import _logger
from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.risk.futures_collateral import bound_futures_account_store
from thytrader.trading.models import DeploymentMode

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.alerts.service import AlertService
    from thytrader.execution.futures_live import FuturesLiveAccount
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.trading.models import DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore


async def supervise_futures(
    *,
    alert_service: AlertService | None,
    store: ExecutionStore,
    policy: RiskPolicyDefinition,
    observed_at: datetime,
) -> Literal["observed", "failed", "disabled"]:
    """Report failure distinctly and never apply all-clear after an incomplete read.

    Spot-only inventories perform no CFM read. An injected live source reuses the cycle's
    shared account read. Production remains spot-only and uses the existing durable mirror.
    No broker is constructed, no order is sent, and no book or policy is modified here.
    """
    if alert_service is None:
        return "disabled"
    try:
        audit = bound_execution_audit_store()
        if audit is not None:
            for event in await audit.list_recent(limit=500):
                finding = orphan_finding(event, now=observed_at)
                if finding is not None:
                    await alert_service.apply(
                        (finding,), now=event.occurred_at, evaluated=(), dispatch=False
                    )
        deployments = await store.list_deployments()
        snapshots = tuple(
            [
                await store.get_accounting_snapshot(row.id)
                for row in deployments
                if row.mode is DeploymentMode.LIVE and is_futures_product_id(row.product_id)
            ]
        )
        books = tuple(book for book in snapshots if visibility_scope(book) is not False)
        account, failed = await _account(books, snapshots)
        evidence = futures_safety_evidence(
            snapshots, account=account, policy=policy, now=observed_at, observation_failed=failed
        )
        await alert_service.apply(
            evidence.findings,
            now=observed_at,
            evaluated=evidence.evaluated,
            dispatch=False,
        )
    except Exception as error:  # noqa: BLE001 - observer failures never fail execution.
        _logger.warning("futures_supervision_failed type=%s", type(error).__name__)
        try:
            await alert_service.apply(
                (
                    SupervisionFinding(
                        code=AlertCode.FUTURES_ACCOUNT_UNKNOWN,
                        scope=AlertScope.FLEET,
                        subject="futures:live",
                        severity=AlertSeverity.CRITICAL,
                        detail=(
                            "The futures safety observer failed; account supervision is unknown, "
                            "not all-clear."
                        ),
                    ),
                ),
                now=observed_at,
                evaluated=(),
                dispatch=False,
            )
        except Exception as storage_error:  # noqa: BLE001 - isolate the unavailable alert store.
            _logger.warning("futures_observer_alert_failed type=%s", type(storage_error).__name__)
        return "failed"
    return "failed" if failed else "observed"


async def _account(
    books: tuple[DeploymentSnapshot, ...],
    snapshots: tuple[DeploymentSnapshot, ...],
) -> tuple[FuturesLiveAccount | None, bool]:
    """Failed account reads still produce unknown evidence for a known held inventory."""
    if any(visibility_scope(book) is None for book in snapshots):
        return None, True
    if not books:
        return None, False
    try:
        if current_live_futures_runtime() is not None:
            return await prepare_live_futures_book(books[0], portfolio=snapshots), False
        runtime = current_futures_runtime()
        return await mirror_live_account(
            bound_futures_account_store(),
            None if runtime is None else runtime.observations,
            books,
        ), False
    except Exception as error:  # noqa: BLE001 - isolate malformed/unavailable account evidence.
        _logger.warning("futures_account_observation_failed type=%s", type(error).__name__)
        return None, True
