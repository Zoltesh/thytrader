"""Strategy, deployment, and runtime operator reports."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal  # noqa: TC003 - runtime marks map uses Decimal at runtime
from typing import TYPE_CHECKING

from thytrader import __version__
from thytrader.credentials.service import credentials_are_configured
from thytrader.execution.book_marks import last_bar_marks
from thytrader.execution.user_feed_state import UserOrderFeedUnavailableError
from thytrader.operator.diagnostics.common import _runtime_timeframe, _supported_clock
from thytrader.operator.models import (
    STANDARD_REDACTION,
    ComponentReport,
    ReportStatus,
    SupportedTimeframe,
)
from thytrader.operator.runtime_models import (
    DeploymentBookSummary,
    DeploymentSummary,
    ReconciliationFinding,
    ReconciliationReport,
    RiskFinding,
    RiskReport,
    RuntimePayload,
    RuntimeReport,
    StrategiesPayload,
    StrategiesReport,
    StrategySummary,
    UserOrderFeedPayload,
)
from thytrader.operator.status import aggregate_status, recommend_next_action
from thytrader.strategies.library import StrategyLibraryError
from thytrader.strategies.models import covered_product_ids
from thytrader.strategies.snapshots import StrategySnapshotError
from thytrader.trading.ledger import ledger_from_snapshot
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    DeploymentSummarySnapshot,
    ExecutionStoreError,
    resolved_product_id,
    snapshot_positions,
    summary_as_snapshot,
    visible_instrument_runtimes,
)
from thytrader.trading.protection import (
    PositionState,
    book_exit_in_flight,
    book_position_state,
    book_protection_evidence,
    deployment_position_state,
    protection_evidence_response,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from uuid import UUID

    from thytrader.operator.service import OperatorDiagnostics


_MAX_REPORT_STRATEGIES = 100


async def build_strategies_report(diagnostics: OperatorDiagnostics) -> StrategiesReport:
    """List strategies and deployments without documents, cash, or fills."""
    now = datetime.now(UTC)
    components: list[ComponentReport] = []
    warnings: list[str] = []
    strategy_rows = await _strategy_summaries(diagnostics, components, warnings)
    deployments = await _deployment_summaries(diagnostics, components, warnings)
    if not components:
        components.append(
            ComponentReport(
                name="strategies",
                status=ReportStatus.HEALTHY,
                reason_code="OK",
                detail="Strategies and deployments were listed.",
            )
        )
    return StrategiesReport(
        application_version=__version__,
        generated_at=now,
        overall_status=aggregate_status(components),
        components=tuple(components),
        redaction=STANDARD_REDACTION,
        partial_result_warnings=tuple(warnings),
        recommended_next_action=recommend_next_action(components),
        payload=StrategiesPayload(
            strategies=strategy_rows,
            deployments=deployments,
        ),
    )


async def build_runtime_report(
    diagnostics: OperatorDiagnostics, deployment_id: UUID | None = None
) -> RuntimeReport:
    """Combine deployment status with risk and reconciliation findings."""
    now = datetime.now(UTC)
    strategies = await diagnostics.strategies()
    risk = await diagnostics.risk()
    reconciliation = await diagnostics.reconciliation()
    deployments, risk_findings, recon_findings, extra = _runtime_slice(
        strategies,
        risk,
        reconciliation,
        deployment_id,
    )
    components = (
        *strategies.components,
        *risk.components,
        *reconciliation.components,
        *extra,
        *(await _execution_market_data_components(diagnostics)),
    )
    return RuntimeReport(
        application_version=__version__,
        generated_at=now,
        overall_status=aggregate_status(components),
        components=components,
        redaction=STANDARD_REDACTION,
        partial_result_warnings=(
            *strategies.partial_result_warnings,
            *risk.partial_result_warnings,
            *reconciliation.partial_result_warnings,
        ),
        recommended_next_action=recommend_next_action(components),
        payload=RuntimePayload(
            deployments=deployments,
            risk_findings=risk_findings,
            reconciliation_findings=recon_findings,
            user_order_feed=await _user_order_feed_payload(diagnostics),
        ),
    )


async def _execution_market_data_components(
    diagnostics: OperatorDiagnostics,
) -> tuple[ComponentReport, ...]:
    """Disclose when paper books evaluate synthetic demo candles instead of venue prices.

    The execution worker follows the same shared credentials as this process, so
    absent credentials here mean it runs ``DemoMarketData`` and has no live broker.
    """
    if credentials_are_configured(diagnostics.settings):
        return ()
    try:
        deployments = await diagnostics.execution.list_deployments()
    except Exception:  # noqa: BLE001 - deployment listing failures stay partial.
        return ()
    active_paper = [
        item
        for item in deployments
        if item.mode is DeploymentMode.PAPER and item.status is not DeploymentStatus.STOPPED
    ]
    if not active_paper:
        return ()
    return (
        ComponentReport(
            name="execution_market_data",
            status=ReportStatus.HEALTHY,
            reason_code="DEMO_MARKET_DATA",
            detail=(
                f"Coinbase credentials are absent: {len(active_paper)} paper deployment(s) "
                "evaluate synthetic demo candles, not Coinbase prices. Live deployments "
                "pause (Live broker is unavailable.)."
            ),
        ),
    )


async def _user_order_feed_payload(diagnostics: OperatorDiagnostics) -> UserOrderFeedPayload | None:
    """Load the singleton user-order feed snapshot when durable state exists."""
    store = diagnostics.user_order_feed
    if store is None:
        return None
    try:
        snapshot = await store.get()
    except UserOrderFeedUnavailableError:
        return None
    if snapshot is None:
        return None
    return UserOrderFeedPayload(
        state=snapshot.state.value,
        last_message_at=snapshot.last_message_at,
        last_heartbeat_at=snapshot.last_heartbeat_at,
    )


async def _strategy_summaries(
    diagnostics: OperatorDiagnostics,
    components: list[ComponentReport],
    warnings: list[str],
) -> tuple[StrategySummary, ...]:
    """List up to ``_MAX_REPORT_STRATEGIES`` strategies or record a partial result."""
    try:
        page = await diagnostics.strategies_store.list_page(limit=_MAX_REPORT_STRATEGIES, offset=0)
    except StrategyLibraryError:
        components.append(
            ComponentReport(
                name="strategies",
                status=ReportStatus.DEGRADED,
                reason_code="STRATEGIES_UNAVAILABLE",
                detail="Strategies could not be listed.",
            )
        )
        warnings.append("Strategy listing failed; runtime rows may still be complete.")
        return ()
    if page.total > len(page.records):
        warnings.append(
            f"Showing the {len(page.records)} most recently updated of {page.total} "
            "strategies; use thytrader-research list-strategies to page."
        )
    return tuple(
        StrategySummary(
            strategy_id=record.strategy_id,
            name=record.name,
            revision=record.revision,
            valid=record.validation.valid,
            current_fingerprint=record.current_fingerprint,
            product_id=record.product_id,
            timeframe=_supported_clock(record.timeframe),
            updated_at=record.updated_at,
        )
        for record in page.records
    )


async def _deployment_summaries(
    diagnostics: OperatorDiagnostics,
    components: list[ComponentReport],
    warnings: list[str],
) -> tuple[DeploymentSummary, ...]:
    """List deployments without cash or order payloads."""
    del components, warnings
    deployments = await diagnostics.execution.list_deployments()
    extra = await _covered_products_by_fingerprint(diagnostics, deployments)
    summaries: list[DeploymentSummary] = []
    for item in deployments:
        summary_row = await _summary_or_none(diagnostics, item.id)
        snapshot = summary_as_snapshot(summary_row) if summary_row else None
        marks = (
            await last_bar_marks(diagnostics.decision_store, snapshot)
            if diagnostics.decision_store is not None and snapshot is not None
            else {}
        )
        extra_ids = extra.get(item.strategy_fingerprint or "", (item.product_id,))
        summaries.append(
            _deployment_summary(
                item,
                snapshot=snapshot,
                summary_row=summary_row,
                marks={product_id: mark.price for product_id, mark in marks.items()},
                extra_product_ids=extra_ids,
                timeframe=await _runtime_timeframe(diagnostics, item),
            )
        )
    return tuple(summaries)


async def _summary_or_none(
    diagnostics: OperatorDiagnostics, deployment_id: UUID
) -> DeploymentSummarySnapshot | None:
    """Load one bounded summary or omit books when execution storage fails."""
    try:
        return await diagnostics.execution.get_deployment_summary(deployment_id)
    except ExecutionStoreError:
        return None


async def _covered_products_by_fingerprint(
    diagnostics: OperatorDiagnostics, deployments: tuple[Deployment, ...]
) -> dict[str, tuple[str, ...]]:
    """Map each deployment's snapshot onto its covered product ids."""
    covered: dict[str, tuple[str, ...]] = {}
    for fingerprint in {item.strategy_fingerprint for item in deployments}:
        if fingerprint is None:
            continue
        try:
            snapshot = await diagnostics.publications.load(fingerprint)
        except StrategySnapshotError, RuntimeError, TypeError, ValueError:
            continue
        covered[fingerprint] = covered_product_ids(snapshot.definition)
    return covered


def _deployment_summary(
    deployment: Deployment,
    *,
    snapshot: DeploymentSnapshot | None = None,
    summary_row: DeploymentSummarySnapshot | None = None,
    marks: Mapping[str, Decimal] | None = None,
    extra_product_ids: tuple[str, ...] = (),
    timeframe: SupportedTimeframe | None = None,
) -> DeploymentSummary:
    """Project one deployment without cash or quantities."""
    ledger = None
    state: PositionState | None = None
    if snapshot is not None:
        ledger = ledger_from_snapshot(snapshot, marks=marks)
        state = deployment_position_state(snapshot)
    return DeploymentSummary(
        deployment_id=deployment.id,
        kind=deployment.kind.value,
        strategy_id=deployment.strategy_id,
        strategy_fingerprint=deployment.strategy_fingerprint,
        strategy_name=deployment.strategy_name,
        strategy_deleted=deployment.strategy_deleted,
        timeframe=timeframe if timeframe is not None else _supported_clock(deployment.timeframe),
        mode=deployment.mode.value,
        status=deployment.status.value,
        phase=deployment.phase.value,
        position_state=None if state is None else state.value,
        exit_in_flight=None if state is None else state is PositionState.EXITING,
        product_id=deployment.product_id,
        last_evaluated_bar=deployment.last_evaluated_bar,
        mismatch_present=bool(deployment.mismatch_detail),
        last_signal=deployment.last_signal,
        books=_book_summaries(
            snapshot, extra_product_ids=extra_product_ids or (deployment.product_id,)
        ),
        lifecycle_command=deployment.lifecycle_command.value,
        daily_loss_latched=deployment.daily_loss_latched,
        drawdown_latched=deployment.drawdown_latched,
        revision=deployment.revision,
        worker_lease_held=bool(
            deployment.worker_lease_holder is not None
            and deployment.worker_lease_expires_at is not None
        ),
        ledger_mark_complete=None if ledger is None else ledger.mark_complete,
        open_book_count=(None if summary_row is None else summary_row.book_totals.open_books),
    )


def _book_summaries(
    snapshot: DeploymentSnapshot | None,
    *,
    extra_product_ids: tuple[str, ...],
) -> tuple[DeploymentBookSummary, ...]:
    """Project phase, side and quantitative stop evidence, omitting prices and cash."""
    if snapshot is None:
        return ()
    positions = {
        resolved_product_id(item.product_id, snapshot.deployment): item
        for item in snapshot_positions(snapshot)
    }
    rows: list[DeploymentBookSummary] = []
    for runtime in visible_instrument_runtimes(snapshot, extra_product_ids=extra_product_ids):
        position = positions.get(runtime.product_id)
        evidence = book_protection_evidence(
            snapshot, product_id=runtime.product_id, position=position
        )
        rows.append(
            DeploymentBookSummary(
                product_id=runtime.product_id,
                phase=runtime.phase.value,
                side=None if position is None else position.side.value,
                protection_status=evidence.status.value,
                protection=protection_evidence_response(evidence),
                position_state=book_position_state(
                    snapshot,
                    product_id=runtime.product_id,
                    position=position,
                    phase=runtime.phase,
                    evidence=evidence,
                ).value,
                exit_in_flight=book_exit_in_flight(
                    snapshot, product_id=runtime.product_id, position=position
                ),
            )
        )
    return tuple(rows)


def _runtime_slice(
    strategies: StrategiesReport,
    risk: RiskReport,
    reconciliation: ReconciliationReport,
    deployment_id: UUID | None,
) -> tuple[
    tuple[DeploymentSummary, ...],
    tuple[RiskFinding, ...],
    tuple[ReconciliationFinding, ...],
    tuple[ComponentReport, ...],
]:
    """Optionally restrict runtime evidence to one deployment identity."""
    deployments = strategies.payload.deployments
    risk_findings = risk.payload.findings
    recon_findings = reconciliation.payload.findings
    if deployment_id is None:
        return deployments, risk_findings, recon_findings, ()
    selected = tuple(item for item in deployments if item.deployment_id == deployment_id)
    extra: tuple[ComponentReport, ...] = ()
    if not selected:
        extra = (
            ComponentReport(
                name="runtime",
                status=ReportStatus.FAILED,
                reason_code="DEPLOYMENT_NOT_FOUND",
                detail="No deployment matched the requested id.",
            ),
        )
    return (
        selected,
        tuple(item for item in risk_findings if item.deployment_id == deployment_id),
        tuple(item for item in recon_findings if item.deployment_id == deployment_id),
        extra,
    )
