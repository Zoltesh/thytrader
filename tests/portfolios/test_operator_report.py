"""The operator ``portfolios`` report shows deployment, breaker, and proposal state (ADR 0091)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.portfolios.runtime_support import operator, portfolio, world
from thytrader.operator.models import ReportStatus
from thytrader.operator.portfolios_report import build_portfolios_report

pytestmark = pytest.mark.anyio


async def test_the_report_degrades_on_a_latched_breaker_and_counts_proposals() -> None:
    """Running portfolios report their state; a latched breaker is a degraded component."""
    state = world()
    current, _records = await portfolio(state)
    pid = current.portfolio.portfolio_id
    report = await build_portfolios_report(state.portfolios, state.execution)
    (digest,) = report.payload.portfolios
    assert (digest.deployable, digest.deployment_state, digest.breaker_latched) == (
        True,
        "not_deployed",
        False,
    )
    assert report.components[0].reason_code == "OK"
    await state.runtime.start(pid, revision=current.portfolio.revision, context=operator())
    runtime = await state.portfolios.runtime_state(pid)
    await state.portfolios.write_runtime(
        replace(
            runtime,
            breaker_reason="PORTFOLIO_DAILY_LOSS_STOP",
            breaker_detail="Lost 50 USDC today.",
            breaker_latched_at=runtime.run_started_at,
        ),
        expected_revision=runtime.revision,
    )
    latched = await build_portfolios_report(state.portfolios, state.execution)
    (digest,) = latched.payload.portfolios
    assert digest.deployment_state == "running"
    assert (digest.breaker_latched, digest.breaker_reason_code) == (
        True,
        "PORTFOLIO_DAILY_LOSS_STOP",
    )
    assert latched.overall_status is ReportStatus.DEGRADED
    assert latched.components[0].reason_code == "PORTFOLIO_BREAKER_LATCHED"
    assert digest.pending_proposals == 0
