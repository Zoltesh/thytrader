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
    "DATABASE_UNREACHABLE": "Verify PostgreSQL is running and THYTRADER_DATABASE_URL is valid.",
    "API_NOT_READY": "Wait for thytrader-api startup, then GET /health/ready.",
    "API_UNREACHABLE": "Start thytrader-api on the configured loopback port and retry.",
    "NOT_READY": "Start the named worker process and confirm its readiness file exists.",
    "EXCHANGE_UNAVAILABLE": "Check Coinbase connectivity without printing credentials.",
    "MARKET_DATA_STATE_UNAVAILABLE": "Confirm the market-data worker can write PostgreSQL state.",
    "MARKET_DATA_NEVER_RUN": "Start thytrader-market-data-worker and wait for verified coverage.",
    "GAPS_PRESENT": "Inspect market-data ingestion diagnostics for the product.",
    "STALE": "Wait for the next verified 1h publication or inspect ingestion failures.",
    "RESULT_NOT_FOUND": "Pass a published result fingerprint from thytrader-operator performance.",
    "RISK_REGISTRY_UNAVAILABLE": "Treat pause and mismatch findings as the current risk surface.",
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
