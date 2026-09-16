"""Aggregate component outcomes into report status and process exit codes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.operator.models import ComponentReport, ReportStatus

if TYPE_CHECKING:
    from collections.abc import Sequence

EXIT_HEALTHY = 0
EXIT_DEGRADED = 1
EXIT_FAILED = 2
EXIT_USAGE = 3

_RECOMMENDATIONS: dict[str, str] = {
    "DATABASE_UNCONFIGURED": "Set THYTRADER_DATABASE_URL and apply migrations, then re-run health.",
    "DATABASE_ENGINE_MISSING": (
        "Rebuild and restart with `make run` so the API can ping PostgreSQL."
    ),
    "DATABASE_UNREACHABLE": "Verify PostgreSQL is running and THYTRADER_DATABASE_URL is valid.",
    "API_NOT_READY": "Wait for thytrader-api startup, then GET /health/ready.",
    "API_UNREACHABLE": "Start thytrader-api on the configured loopback port and retry.",
    "NOT_READY": "Start the named worker process and confirm it is heartbeating.",
    "HEARTBEAT_UNAVAILABLE": (
        "Set THYTRADER_DATABASE_URL and apply migrations (0016) so operator health "
        "can see worker heartbeats."
    ),
    "HEARTBEAT_MISSING": (
        "Start the named worker process. Docker /tmp readiness files are not health."
    ),
    "HEARTBEAT_STALE": "Restart the named worker; its last heartbeat is older than two loops.",
    "EXCHANGE_UNAVAILABLE": "Check Coinbase connectivity without printing credentials.",
    "MARKET_DATA_STATE_UNAVAILABLE": "Confirm the market-data worker can write PostgreSQL state.",
    "MARKET_DATA_NEVER_RUN": "Start thytrader-market-data-worker and wait for verified coverage.",
    "GAPS_PRESENT": "Inspect market-data ingestion diagnostics for the product.",
    "STALE": "Wait for the next verified 1h publication or inspect ingestion failures.",
    "RESULT_NOT_FOUND": "Pass a published result fingerprint from thytrader-operator performance.",
    "FINDINGS_PRESENT": "Inspect paused or mismatched deployments before starting new risk.",
    "DEPLOYMENT_NOT_FOUND": "Pass a deployment id from thytrader-operator strategies or runtime.",
    "MEMORY_STORAGE_UNAVAILABLE": (
        "Set THYTRADER_DATABASE_URL and apply migration 0023, then re-run monitor."
    ),
    "EXECUTION_UNAVAILABLE": "Set THYTRADER_DATABASE_URL so monitor can list deployments.",
    "NOTIFICATION_FAILED": (
        "Inspect recent notification delivery_status without printing webhook URLs."
    ),
    "DEPLOYMENT_PAUSED": "Inspect thytrader-operator runtime before new risk-increasing orders.",
    "DEPLOYMENT_MISMATCH": (
        "Inspect thytrader-operator reconciliation before new risk-increasing orders."
    ),
    "DAILY_LOSS_LIMIT": (
        "Daily-loss breaker paused risk-increasing orders. Exits continue. Resume after the "
        "UTC day recovers or publish a tighter/looser policy with --confirm."
    ),
    "STRATEGY_DRAWDOWN_LIMIT": (
        "Drawdown breaker paused this strategy's risk-increasing orders. Exits continue. "
        "Resume after equity recovers or publish a new risk policy with --confirm."
    ),
}


def aggregate_status(components: Sequence[ComponentReport]) -> ReportStatus:
    """Fail closed when telemetry is missing; otherwise take the worst component."""
    if not components:
        return ReportStatus.FAILED
    if any(component.status is ReportStatus.FAILED for component in components):
        return ReportStatus.FAILED
    if any(component.status is ReportStatus.DEGRADED for component in components):
        return ReportStatus.DEGRADED
    return ReportStatus.HEALTHY


def recommend_next_action(components: Sequence[ComponentReport]) -> str:
    """Suggest the first failed, then first degraded, diagnostic follow-up."""
    for desired in (ReportStatus.FAILED, ReportStatus.DEGRADED):
        match = next(
            (component for component in components if component.status is desired),
            None,
        )
        if match is not None:
            known = _RECOMMENDATIONS.get(match.reason_code)
            if known is not None:
                return known
            return f"Investigate {match.name} ({match.reason_code})."
    return "No action required."


def exit_code_for(status: ReportStatus) -> int:
    """Map overall status to a stable CLI exit code."""
    if status is ReportStatus.HEALTHY:
        return EXIT_HEALTHY
    if status is ReportStatus.DEGRADED:
        return EXIT_DEGRADED
    return EXIT_FAILED
