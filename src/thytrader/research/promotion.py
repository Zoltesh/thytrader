"""Derived research-to-runtime promotion evidence.

This report does not mutate canonical backtest or study bytes. Sweep-scored
windows stay in ``parameter_sweep_candidates`` and are never labeled OOS.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from thytrader.execution.models import DeploymentMode
from thytrader.research.studies import WindowRole

if TYPE_CHECKING:
    from thytrader.execution.models import Deployment
    from thytrader.persistence.backtest_results import BacktestResultSummaryView
    from thytrader.research.studies import ResearchStudy

PROMOTION_EVIDENCE_VERSION = "thytrader-promotion-evidence-v1"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"


class _FrozenEvidence(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceWindow(_FrozenEvidence):
    """One child-window or full-window PnL headline."""

    label: str
    role: str
    study_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    result_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    strategy_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    total_net_pnl: str
    trade_count: int = Field(ge=0)


class EvidenceDeployment(_FrozenEvidence):
    """One paper or live runtime bound to the requested strategy identity."""

    deployment_id: str
    mode: str
    status: str
    product_id: str


class PromotionEvidence(_FrozenEvidence):
    """IS vs OOS vs sweep vs paper vs live evidence for one strategy fingerprint."""

    schema_version: Literal["thytrader-promotion-evidence-v1"] = PROMOTION_EVIDENCE_VERSION
    strategy_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    full_window_backtests: tuple[EvidenceWindow, ...] = ()
    in_sample: tuple[EvidenceWindow, ...] = ()
    out_of_sample: tuple[EvidenceWindow, ...] = ()
    parameter_sweep_candidates: tuple[EvidenceWindow, ...] = ()
    paper_deployments: tuple[EvidenceDeployment, ...] = ()
    live_deployments: tuple[EvidenceDeployment, ...] = ()


def assemble_promotion_evidence(
    *,
    strategy_fingerprint: str,
    full_window_backtests: tuple[BacktestResultSummaryView, ...],
    studies: tuple[ResearchStudy, ...],
    deployments: tuple[Deployment, ...],
) -> PromotionEvidence:
    """Project bounded promotion evidence without claiming sweep means as OOS."""
    in_sample, out_of_sample, sweep_candidates = _classify_study_windows(
        strategy_fingerprint, studies
    )
    paper, live = _classify_deployments(strategy_fingerprint, deployments)
    return PromotionEvidence(
        strategy_fingerprint=strategy_fingerprint,
        full_window_backtests=_full_window_rows(strategy_fingerprint, full_window_backtests),
        in_sample=in_sample,
        out_of_sample=out_of_sample,
        parameter_sweep_candidates=sweep_candidates,
        paper_deployments=paper,
        live_deployments=live,
    )


def _full_window_rows(
    strategy_fingerprint: str,
    full_window_backtests: tuple[BacktestResultSummaryView, ...],
) -> tuple[EvidenceWindow, ...]:
    """Copy matching full-window backtest headlines."""
    return tuple(
        EvidenceWindow(
            label="full_window",
            role="full_window",
            result_fingerprint=row.result_fingerprint,
            strategy_fingerprint=row.strategy_fingerprint,
            total_net_pnl=row.summary.total_net_pnl,
            trade_count=row.summary.trade_count,
        )
        for row in full_window_backtests
        if row.strategy_fingerprint == strategy_fingerprint
    )


def _classify_study_windows(
    strategy_fingerprint: str,
    studies: tuple[ResearchStudy, ...],
) -> tuple[tuple[EvidenceWindow, ...], tuple[EvidenceWindow, ...], tuple[EvidenceWindow, ...]]:
    """Bucket child windows into IS, genuine OOS, and sweep candidates."""
    in_sample: list[EvidenceWindow] = []
    out_of_sample: list[EvidenceWindow] = []
    sweep_candidates: list[EvidenceWindow] = []
    buckets = {
        WindowRole.IN_SAMPLE: in_sample,
        WindowRole.OUT_OF_SAMPLE: out_of_sample,
        WindowRole.SWEEP_CANDIDATE: sweep_candidates,
    }
    for study in studies:
        for window in study.windows:
            if window.strategy_fingerprint != strategy_fingerprint:
                continue
            bucket = buckets.get(window.role)
            if bucket is None:
                continue
            bucket.append(
                EvidenceWindow(
                    label=window.label,
                    role=window.role.value,
                    study_fingerprint=study.study_fingerprint,
                    result_fingerprint=window.result_fingerprint,
                    strategy_fingerprint=window.strategy_fingerprint,
                    total_net_pnl=window.summary.total_net_pnl,
                    trade_count=window.summary.trade_count,
                )
            )
    return tuple(in_sample), tuple(out_of_sample), tuple(sweep_candidates)


def _classify_deployments(
    strategy_fingerprint: str,
    deployments: tuple[Deployment, ...],
) -> tuple[tuple[EvidenceDeployment, ...], tuple[EvidenceDeployment, ...]]:
    """Bucket paper and live runtimes for the requested strategy fingerprint."""
    paper: list[EvidenceDeployment] = []
    live: list[EvidenceDeployment] = []
    for deployment in deployments:
        if deployment.strategy_fingerprint != strategy_fingerprint:
            continue
        row = EvidenceDeployment(
            deployment_id=str(deployment.id),
            mode=deployment.mode.value,
            status=deployment.status.value,
            product_id=deployment.product_id,
        )
        if deployment.mode is DeploymentMode.PAPER:
            paper.append(row)
        elif deployment.mode is DeploymentMode.LIVE:
            live.append(row)
    return tuple(paper), tuple(live)
