"""Operator status aggregation tests."""

from thytrader.operator.models import ComponentReport, ReportStatus
from thytrader.operator.status import (
    EXIT_DEGRADED,
    EXIT_FAILED,
    EXIT_HEALTHY,
    aggregate_status,
    exit_code_for,
    recommend_next_action,
)


def test_empty_components_are_failed_not_healthy() -> None:
    """Missing telemetry must not be reported as a healthy instance."""
    assert aggregate_status(()) is ReportStatus.FAILED
    assert exit_code_for(ReportStatus.FAILED) == EXIT_FAILED


def test_worst_component_wins() -> None:
    """Failed outranks degraded, which outranks healthy."""
    healthy = ComponentReport(name="a", status=ReportStatus.HEALTHY, reason_code="OK")
    degraded = ComponentReport(name="b", status=ReportStatus.DEGRADED, reason_code="NOT_READY")
    failed = ComponentReport(
        name="c",
        status=ReportStatus.FAILED,
        reason_code="DATABASE_UNREACHABLE",
    )
    assert aggregate_status((healthy, degraded)) is ReportStatus.DEGRADED
    assert aggregate_status((healthy, failed)) is ReportStatus.FAILED
    assert exit_code_for(ReportStatus.HEALTHY) == EXIT_HEALTHY
    assert exit_code_for(ReportStatus.DEGRADED) == EXIT_DEGRADED


def test_recommendation_uses_first_failed_reason() -> None:
    """Operators get a stable next action from the first failed component."""
    failed = ComponentReport(
        name="database",
        status=ReportStatus.FAILED,
        reason_code="DATABASE_UNREACHABLE",
    )
    assert "PostgreSQL" in recommend_next_action((failed,))
