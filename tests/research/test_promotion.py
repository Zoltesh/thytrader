"""Promotion evidence must separate IS, OOS, sweep candidates, paper, and live."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from thytrader.backtest.models import BacktestSummary
from thytrader.execution.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    RuntimePhase,
)
from thytrader.persistence.backtest_results import BacktestResultSummaryView
from thytrader.research.promotion import assemble_promotion_evidence
from thytrader.research.studies import (
    ResearchStudy,
    StudyAggregate,
    StudyKind,
    StudyWindowResult,
    WindowRole,
)


def _summary(*, pnl: str, trades: int) -> BacktestSummary:
    """Return one compact child-window summary."""
    return BacktestSummary(
        initial_equity="10000",
        final_equity="10100",
        total_net_pnl=pnl,
        total_return_fraction="0.01",
        gross_profit=pnl if not pnl.startswith("-") else "0",
        gross_loss="0" if not pnl.startswith("-") else pnl.lstrip("-"),
        win_rate="1",
        trade_count=trades,
        winning_trade_count=trades,
        maximum_drawdown="0",
        maximum_drawdown_fraction="0",
        exposure_bars=10,
        evaluation_bars=10,
    )


def _window(*, role: WindowRole, label: str, pnl: str, fingerprint: str) -> StudyWindowResult:
    """Return one study window headline used by promotion evidence."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 2, 1, tzinfo=UTC)
    return StudyWindowResult(
        label=label,
        role=role,
        fold_index=0,
        product_id="UNI-USDC",
        run_fingerprint=fingerprint,
        result_fingerprint=fingerprint,
        strategy_fingerprint="sha256:" + "a" * 64,
        evaluation_start=start,
        evaluation_end=end,
        summary=_summary(pnl=pnl, trades=3),
    )


def test_promotion_evidence_does_not_label_sweep_candidates_as_oos() -> None:
    """Parameter-sweep scored windows stay in candidates, never out_of_sample."""
    strategy_fingerprint = "sha256:" + "a" * 64
    study = ResearchStudy(
        study_fingerprint="sha256:" + "b" * 64,
        request_fingerprint="sha256:" + "c" * 64,
        kind=StudyKind.PARAMETER_SWEEP,
        engine_contract_version="thytrader-bar-backtest-v4",
        windows=(
            _window(
                role=WindowRole.SWEEP_CANDIDATE,
                label="rsi-10",
                pnl="22.85",
                fingerprint="sha256:" + "1" * 64,
            ),
            _window(
                role=WindowRole.IN_SAMPLE,
                label="is",
                pnl="-47.47",
                fingerprint="sha256:" + "2" * 64,
            ),
            _window(
                role=WindowRole.OUT_OF_SAMPLE,
                label="oos",
                pnl="73.85",
                fingerprint="sha256:" + "3" * 64,
            ),
        ),
        aggregate=StudyAggregate(
            window_count=3,
            oos_window_count=1,
            oos_trade_count=3,
            oos_winning_trade_count=3,
            candidate_window_count=1,
            candidate_trade_count=3,
            candidate_winning_trade_count=3,
        ),
    )
    backtest = BacktestResultSummaryView(
        result_fingerprint="sha256:" + "4" * 64,
        run_fingerprint="sha256:" + "5" * 64,
        strategy_fingerprint=strategy_fingerprint,
        dataset_fingerprint="sha256:" + "6" * 64,
        engine_contract_version="thytrader-bar-backtest-v4",
        published_at=datetime(2026, 9, 19, tzinfo=UTC),
        summary=_summary(pnl="10.88", trades=27),
    )
    paper = Deployment(
        id=UUID("01978a3e-5f2c-7d10-b3a4-0000000000aa"),
        strategy_fingerprint=strategy_fingerprint,
        strategy_id=UUID("01978a3e-5f2c-7d10-b3a4-0000000000bb"),
        product_id="UNI-USDC",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("10000"),
        phase=RuntimePhase.FLAT,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        updated_at=datetime(2026, 9, 1, tzinfo=UTC),
        kind=DeploymentKind.STRATEGY,
    )
    evidence = assemble_promotion_evidence(
        strategy_fingerprint=strategy_fingerprint,
        full_window_backtests=(backtest,),
        studies=(study,),
        deployments=(paper,),
    )
    assert evidence.schema_version == "thytrader-promotion-evidence-v1"
    assert [row.total_net_pnl for row in evidence.full_window_backtests] == ["10.88"]
    assert [row.role for row in evidence.in_sample] == ["in_sample"]
    assert [row.role for row in evidence.out_of_sample] == ["out_of_sample"]
    assert [row.role for row in evidence.parameter_sweep_candidates] == ["sweep_candidate"]
    assert "oos" not in {row.label for row in evidence.parameter_sweep_candidates}
    assert len(evidence.paper_deployments) == 1
    assert evidence.paper_deployments[0].status == "running"
    assert evidence.live_deployments == ()
