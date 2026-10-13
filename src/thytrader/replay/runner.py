"""Offline calculation boundaries; observed ledger reproduction is not execution parity."""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Literal

from thytrader.decimal_text import canonical_decimal
from thytrader.evaluation.trace import SignalTraceRecord
from thytrader.replay.configuration import configuration_differences
from thytrader.replay.execution import execute
from thytrader.replay.models import FrozenModel, ReplayPackage
from thytrader.replay.signals import compare_signals
from thytrader.replay.source import installed_source_identity
from thytrader.replay.steps import ExecutionOutcome
from thytrader.replay.timing_report import StepTimingReport, timing_report
from thytrader.trading.ledger import LedgerFill, mark_deployment_ledger
from thytrader.trading.models import OrderSide


class Comparison(FrozenModel):
    """One evidence-qualified comparison, never a blanket parity certificate."""

    status: Literal["match", "mismatch", "unavailable"]
    scope: str = "specified_evidence_only"
    reasons: tuple[str, ...] = ()


class LedgerReplay(FrozenModel):
    """Independent cash/quantity reconstruction plus the existing accounting kernel result."""

    status: Literal["match", "mismatch"]
    ending_cash: str
    ending_quantity: str
    total_fees: str
    realized_net_pnl: str
    observed_ending_cash: str


class StepResult(FrozenModel):
    """Actual independently executed state beside the package's expected state."""

    actual: ExecutionOutcome
    expected: ExecutionOutcome
    status: Literal["match", "mismatch"]


class ExecutionReplay(FrozenModel):
    """Recomputed orchestration outcomes, not comparisons of recorded values to themselves."""

    status: Literal["match", "mismatch", "unavailable"]
    steps: tuple[StepResult, ...]


class AcceptanceComparison(FrozenModel):
    """Data-only golden acceptance is separate from historical parity and evidence completeness."""

    status: Literal["match", "mismatch"]
    differences: tuple[str, ...] = ()


class ReplayBoundaries(FrozenModel):
    """Independent comparison seams; no member implies universal execution parity."""

    indicators_signals: Comparison
    sizing_risk: Comparison
    orders_fills: Comparison
    accounting: Comparison


class ReplayReport(FrozenModel):
    """Stable public JSON report for cross-language acceptance."""

    schema_version: Literal["thytrader-replay-report-v1"] = "thytrader-replay-report-v1"
    evidence_kind: Literal["synthetic", "prospective", "historical"]
    original_research_deployed: Comparison
    configuration_differences: tuple[str, ...]
    same_config_research_execution: Comparison
    boundaries: ReplayBoundaries
    build_compatibility: Comparison
    ledger: LedgerReplay | None
    execution: ExecutionReplay
    signals: tuple[SignalTraceRecord, ...]
    timing: tuple[StepTimingReport, ...]
    limitations: tuple[str, ...]
    acceptance: AcceptanceComparison | None = None


async def run(package: ReplayPackage) -> ReplayReport:
    """Recompute observable accounting; refuse inference across missing evidence boundaries."""
    ledger = None
    if package.ledger is not None:
        evidence = package.ledger
        with localcontext() as context:
            context.prec = 64
            cash = Decimal(evidence.starting_cash)
            quantity = Decimal(0)
            fills: list[LedgerFill] = []
            for event in evidence.fills:
                side = OrderSide(event.side)
                signed = Decimal(event.quantity) * (1 if side is OrderSide.BUY else -1)
                quantity += signed
                cash -= signed * Decimal(event.price) + Decimal(event.fee)
                fills.append(
                    LedgerFill(
                        side,
                        Decimal(event.price),
                        Decimal(event.quantity),
                        Decimal(event.fee),
                        event.filled_at,
                    )
                )
            derived = mark_deployment_ledger(
                starting_cash=Decimal(evidence.starting_cash),
                cash=cash,
                fills=fills,
                position_quantity=Decimal(0),
                position_entry_price=None,
                mark_price=None,
            )
            ledger = LedgerReplay(
                status="match" if cash == Decimal(evidence.observed_ending_cash) else "mismatch",
                ending_cash=canonical_decimal(cash),
                ending_quantity=canonical_decimal(quantity),
                total_fees=canonical_decimal(derived.total_fees),
                realized_net_pnl=canonical_decimal(derived.realized_net_pnl),
                observed_ending_cash=evidence.observed_ending_cash,
            )
    results: list[StepResult] = []
    signals: list[SignalTraceRecord] = []
    signal_matches: list[bool] = []
    if package.strategy is not None:
        for step in package.steps:
            actual = await execute(step, package.strategy)
            results.append(
                StepResult(
                    actual=actual,
                    expected=step.expected,
                    status="match" if actual == step.expected else "mismatch",
                )
            )
            record, matched = compare_signals(step, package.strategy)
            if record is not None:
                signals.append(record)
                signal_matches.append(matched)
    comparison = Comparison(
        status="unavailable",
        scope="indicators_and_entry_signal_only",
        reasons=package.unavailable_reasons,
    )
    if signal_matches:
        comparison = Comparison(
            status="match" if all(signal_matches) else "mismatch",
            scope="indicators_and_entry_signal_only",
        )
    execution = ExecutionReplay(
        status=(
            "unavailable"
            if not results
            else "match"
            if all(r.status == "match" for r in results)
            else "mismatch"
        ),
        steps=tuple(results),
    )
    changes = ()
    original = Comparison(
        status="unavailable",
        scope="original_research_evidence",
        reasons=("ORIGINAL_RESEARCH_MISSING",),
    )
    if package.original_strategy is not None and package.strategy is not None:
        changes = configuration_differences(package.original_strategy, package.strategy)
        original = Comparison(
            status="mismatch" if changes else "unavailable",
            scope="original_strategy_rules_only",
            reasons=(
                "DEPLOYED_FINANCIAL_RULES_DIFFER",
                "ORIGINAL_CAPITAL_COST_BUILD_EVIDENCE_MISSING",
            )
            if changes
            else ("ORIGINAL_CAPITAL_COST_BUILD_EVIDENCE_MISSING",),
        )
    return ReplayReport(
        evidence_kind=package.evidence_kind,
        original_research_deployed=original,
        configuration_differences=changes,
        same_config_research_execution=comparison,
        boundaries=ReplayBoundaries(
            indicators_signals=comparison,
            sizing_risk=Comparison(
                status="unavailable"
                if "DYNAMIC_RISK_READS_NOT_CAPTURED" in package.unavailable_reasons
                else execution.status,
                scope="isolated_recorded_outcome_only",
                reasons=("DYNAMIC_RISK_READS_NOT_CAPTURED",)
                if "DYNAMIC_RISK_READS_NOT_CAPTURED" in package.unavailable_reasons
                else ("RISK_DECISION_TRACE_NOT_CAPTURED",),
            ),
            orders_fills=Comparison(
                status=execution.status, scope="isolated_recorded_outcome_only"
            ),
            accounting=Comparison(
                status=ledger.status if ledger is not None else execution.status,
                scope="recorded_fill_cashflow_only"
                if ledger is not None
                else "isolated_recorded_outcome_only",
            ),
        ),
        build_compatibility=Comparison(
            status="match"
            if package.source.execution_build == installed_source_identity()
            else "unavailable",
            scope="captured_python_source_vs_current_python_source_only",
            reasons=()
            if package.source.execution_build == installed_source_identity()
            else ("CAPTURED_BUILD_NOT_VERIFIED_AGAINST_CURRENT",),
        ),
        ledger=ledger,
        execution=execution,
        signals=tuple(signals),
        timing=tuple(timing_report(step) for step in package.steps),
        limitations=(
            "LEDGER_REPRODUCTION_IS_NOT_SIGNAL_OR_EXECUTION_PARITY",
            "CURRENT_BUILD_DIFFERENTIAL_IS_NOT_ORIGINAL_BUILD_PARITY",
            "STEPS_ARE_INDEPENDENT_SEAM_REPLAYS_NOT_CONTINUOUS_WORKER_CYCLES",
            *package.unavailable_reasons,
        ),
    )


def report_exit_code(report: ReplayReport) -> int:
    """Zero means all claimed comparisons ran; one mismatch, three missing required evidence."""
    if (report.ledger is not None and report.ledger.status == "mismatch") or any(
        comparison.status == "mismatch"
        for comparison in (
            report.original_research_deployed,
            report.same_config_research_execution,
            report.execution,
        )
    ):
        return 1
    return 3
