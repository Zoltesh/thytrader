"""Human side-by-side reports retain unavailable comparison boundaries."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from thytrader.replay.runner import ReplayReport


def render(report: ReplayReport) -> str:
    """Show recomputed and observed facts without merging distinct parity questions."""
    lines = [
        f"Evidence: {report.evidence_kind}",
        f"Original research → deployed: {report.original_research_deployed.status}",
        f"Original comparison scope: {report.original_research_deployed.scope}",
        f"Same-config research → execution signals: {report.same_config_research_execution.status}",
        f"Execution orchestration: {report.execution.status}",
        f"Sizing/risk (isolated outcome only): {report.boundaries.sizing_risk.status}",
        f"Orders/fills (isolated outcome only): {report.boundaries.orders_fills.status}",
        f"Accounting ({report.boundaries.accounting.scope}): {report.boundaries.accounting.status}",
        f"Build compatibility: {report.build_compatibility.status}",
        f"Build scope: {report.build_compatibility.scope}",
    ]
    if report.ledger is not None:
        ledger = report.ledger
        lines.extend(
            [
                "Boundary              Replayed             Observed             Result",
                f"Ledger cash           {ledger.ending_cash}             "
                f"{ledger.observed_ending_cash}             {ledger.status}",
                f"Ending quantity       {ledger.ending_quantity}",
                f"Paid fees             {ledger.total_fees}",
                f"Realized net PnL      {ledger.realized_net_pnl}",
            ]
        )
    lines.extend(
        f"Original evidence: {reason}" for reason in report.original_research_deployed.reasons
    )
    lines.extend(f"Configuration difference: {path}" for path in report.configuration_differences)
    lines.extend(
        f"Unavailable: {reason}" for reason in report.same_config_research_execution.reasons
    )
    for index, timing in enumerate(report.timing):
        lines.append(f"Timing[{index}]: normalized broker methods, not wire send/ack")
        if timing.measured is not None:
            observed = timing.measured
            lines.append(
                f"  elapsed_ns={observed.elapsed_ns} process_cpu_ns={observed.computation_cpu_ns} "
                f"database_ns={observed.database_elapsed_ns} venue_ns={observed.venue_elapsed_ns}"
            )
        lines.extend(f"  Unavailable: {reason}" for reason in timing.unavailable_reasons)
    lines.extend(f"Limit: {reason}" for reason in report.limitations)
    return "\n".join(lines) + "\n"
