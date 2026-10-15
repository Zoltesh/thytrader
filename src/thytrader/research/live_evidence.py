"""The evidence recorded with every live start, and when it is thin (ADR 0143).

A live start always records what its exact strategy fingerprint has shown: out-of-sample
study windows (trades and summed net result) and paper books (how many, and the days since
the first was created). Evidence below the risk policy's thresholds is thin: the start warns
with ``LIVE_EVIDENCE_THIN``, or, under the opt-in ``require_live_evidence`` safeguard, is
refused with ``LIVE_EVIDENCE_REQUIRED``. A source that cannot be read counts as thin when a
threshold needs it, since nothing is known from it.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import TYPE_CHECKING, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from thytrader.research.promotion import PromotionEvidence, assemble_promotion_evidence
from thytrader.research.promotion_sources import every_study_of
from thytrader.risk.start_safeguards import LiveEvidenceThresholds
from thytrader.trading.models import DeploymentMode

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

    from thytrader.research.catalog import ResearchStudyCatalog
    from thytrader.research.studies import ResearchStudy
    from thytrader.trading.models import Deployment
    from thytrader.trading.store import ExecutionStore

LIVE_EVIDENCE_VERSION = "thytrader-live-start-evidence-v1"
LIVE_EVIDENCE_THIN = "LIVE_EVIDENCE_THIN"
LIVE_EVIDENCE_REQUIRED = "LIVE_EVIDENCE_REQUIRED"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_DAY = timedelta(days=1)
_EXPONENT_LIMIT = 100
"""Decimal exponents a result may have to be summed (far beyond any real profit or loss)."""
EVIDENCE_READ_SECONDS = 10.0
"""Time each evidence source may take before it is reported unavailable."""

EvidenceSource = Literal["studies", "paper_books"]


class LiveStartEvidence(BaseModel):
    """What one strategy fingerprint had shown when a live start was assessed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["thytrader-live-start-evidence-v1"] = LIVE_EVIDENCE_VERSION
    strategy_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    assessed_at: AwareDatetime
    out_of_sample_windows: int = Field(ge=0)
    out_of_sample_trades: int = Field(ge=0)
    out_of_sample_net_pnl: str | None = Field(
        default=None, description="Summed net result of those windows; None without any."
    )
    paper_deployments: int = Field(ge=0)
    paper_days: str = Field(description="Days since the first paper book, one decimal.")
    thresholds: LiveEvidenceThresholds
    unavailable: tuple[EvidenceSource, ...] = Field(
        default=(),
        description=(
            "Sources that failed, ran out of time or were only partly read; each makes the "
            "evidence thin when a threshold needs that source."
        ),
    )
    thin_reasons: tuple[str, ...] = Field(
        default=(), description="Why the evidence is thin; empty when it meets the thresholds."
    )

    @property
    def thin(self) -> bool:
        """Whether any threshold is unmet, or a source a threshold needs is unavailable."""
        return bool(self.thin_reasons)


def assess_live_start_evidence(
    evidence: PromotionEvidence,
    *,
    paper_created_at: Sequence[datetime],
    thresholds: LiveEvidenceThresholds,
    now: datetime,
    unavailable: Sequence[EvidenceSource] = (),
) -> LiveStartEvidence:
    """Summarize ``evidence`` for a live start and name every unmet threshold."""
    windows = evidence.out_of_sample
    trades = sum(window.trade_count for window in windows)
    exact = _exact_sum([window.total_net_pnl for window in windows])
    if exact is None and "studies" not in unavailable:
        unavailable = (*unavailable, "studies")
    net = Decimal(0) if exact is None else exact
    first_paper = min(paper_created_at, default=None)
    elapsed = timedelta(0) if first_paper is None else max(now - first_paper, timedelta(0))
    # Shown rounded down, so a display of 14.0 never stands for 13 days 23 hours.
    paper_days = Decimal(elapsed.total_seconds() / _DAY.total_seconds()).quantize(
        Decimal("0.1"), rounding=ROUND_DOWN
    )
    reasons = [
        *_research_reasons(
            windows, trades=trades, net=net, thresholds=thresholds, unread="studies" in unavailable
        ),
        *_paper_reasons(
            first_paper,
            elapsed=elapsed,
            paper_days=paper_days,
            thresholds=thresholds,
            unread="paper_books" in unavailable,
        ),
    ]
    return LiveStartEvidence(
        strategy_fingerprint=evidence.strategy_fingerprint,
        assessed_at=now,
        out_of_sample_windows=len(windows),
        out_of_sample_trades=trades,
        out_of_sample_net_pnl=str(net) if windows and exact is not None else None,
        paper_deployments=len(paper_created_at),
        paper_days=str(paper_days),
        thresholds=thresholds,
        unavailable=tuple(unavailable),
        thin_reasons=tuple(reasons),
    )


async def gather_live_start_evidence(
    *,
    strategy_fingerprint: str,
    strategy_id: UUID,
    catalog: ResearchStudyCatalog | None,
    execution: ExecutionStore,
    thresholds: LiveEvidenceThresholds,
    now: datetime,
) -> LiveStartEvidence:
    """Read the fingerprint's studies and paper books and assess them; never raises.

    Gathering is advisory: each source has ``EVIDENCE_READ_SECONDS``, and a source that fails,
    is incomplete, or runs out of time is reported unavailable instead of stopping the start.
    """
    unavailable: list[EvidenceSource] = []
    studies: tuple[ResearchStudy, ...] = ()
    complete = False
    if catalog is not None:
        try:
            async with asyncio.timeout(EVIDENCE_READ_SECONDS):
                studies, complete = await every_study_of(
                    catalog, strategy_fingerprint=strategy_fingerprint, strategy_id=strategy_id
                )
        except Exception:  # noqa: BLE001 - advisory evidence never stops a start.
            complete = False
    if not complete:
        unavailable.append("studies")
    deployments: tuple[Deployment, ...] = ()
    try:
        async with asyncio.timeout(EVIDENCE_READ_SECONDS):
            deployments = await execution.list_by_strategy(str(strategy_id))
    except Exception:  # noqa: BLE001 - advisory evidence never stops a start.
        unavailable.append("paper_books")
    try:
        evidence = assemble_promotion_evidence(
            strategy_fingerprint=strategy_fingerprint,
            full_window_backtests=(),
            studies=studies,
            deployments=deployments,
        )
    except ValidationError:
        evidence = PromotionEvidence(strategy_fingerprint=strategy_fingerprint)
        if "studies" not in unavailable:
            unavailable.append("studies")
    paper = [
        deployment.created_at
        for deployment in deployments
        if deployment.mode is DeploymentMode.PAPER
        and deployment.strategy_fingerprint == strategy_fingerprint
    ]
    return assess_live_start_evidence(
        evidence,
        paper_created_at=paper,
        thresholds=thresholds,
        now=now,
        unavailable=unavailable,
    )


def _research_reasons(
    windows: Sequence[object],
    *,
    trades: int,
    net: Decimal,
    thresholds: LiveEvidenceThresholds,
    unread: bool,
) -> list[str]:
    """Why the out-of-sample research is thin, if the thresholds need research at all."""
    required = thresholds.min_out_of_sample_trades > 0 or thresholds.require_positive_out_of_sample
    if not required:
        return []
    if unread:
        return ["studies could not be read"]
    if not windows:
        return ["no out-of-sample study window for this exact strategy fingerprint"]
    reasons: list[str] = []
    if trades < thresholds.min_out_of_sample_trades:
        reasons.append(
            f"{trades} out-of-sample trades, fewer than {thresholds.min_out_of_sample_trades}"
        )
    if thresholds.require_positive_out_of_sample and net <= 0:
        reasons.append(f"out-of-sample net result {net} is not positive")
    return reasons


def _paper_reasons(
    first_paper: datetime | None,
    *,
    elapsed: timedelta,
    paper_days: Decimal,
    thresholds: LiveEvidenceThresholds,
    unread: bool,
) -> list[str]:
    """Why the paper record is thin, if the thresholds need paper days at all."""
    if thresholds.min_paper_days <= 0:
        return []
    if unread:
        return ["paper books could not be read"]
    if first_paper is None:
        return ["no paper book of this exact strategy fingerprint"]
    if elapsed < timedelta(days=thresholds.min_paper_days):
        return [
            f"{paper_days} days since its first paper book, fewer than {thresholds.min_paper_days}"
        ]
    return []


def _exact_sum(texts: Sequence[str]) -> Decimal | None:
    """The exact sum of decimal results; None when one is unreadable or out of range.

    Summed as integers at the smallest exponent, so near-cancelling results keep their true
    sign at any precision. A result whose exponent is beyond ``_EXPONENT_LIMIT`` (no real
    profit or loss looks like that) makes the research unreadable rather than risk a wrong sign.
    """
    values: list[Decimal] = []
    for text in texts:
        try:
            value = Decimal(text)
        except InvalidOperation:
            return None
        exponent = value.as_tuple().exponent
        if (
            not value.is_finite()
            or not isinstance(exponent, int)
            or exponent < -_EXPONENT_LIMIT
            or value.adjusted() > _EXPONENT_LIMIT
        ):
            return None
        values.append(value)
    if not values:
        return Decimal(0)
    smallest = min(int(value.as_tuple().exponent) for value in values)
    total = sum(_scaled(value, smallest) for value in values)
    digits = tuple(int(digit) for digit in str(abs(total)))
    return Decimal((int(total < 0), digits, smallest))


def _scaled(value: Decimal, exponent: int) -> int:
    """``value`` as an integer count of ``10**exponent`` (exact: its exponent is not smaller)."""
    sign, digits, own = value.as_tuple()
    magnitude = int("".join(str(digit) for digit in digits) or "0") * 10 ** (int(own) - exponent)
    return -magnitude if sign else magnitude


def live_evidence_message(evidence: LiveStartEvidence, *, refused: bool) -> str:
    """The warning, or refusal, for thin evidence, naming every reason."""
    reasons = "; ".join(evidence.thin_reasons)
    if refused:
        return (
            f"{LIVE_EVIDENCE_REQUIRED}: the live evidence for this strategy is thin ({reasons}). "
            "Nothing was started. Gather the evidence (an out-of-sample study, a paper bot of "
            "this exact strategy), or turn off the risk policy's require_live_evidence."
        )
    return (
        f"{LIVE_EVIDENCE_THIN}: the live evidence for this strategy is thin ({reasons}). "
        "Started anyway; the evidence is recorded with the bot. Set the risk policy's "
        "require_live_evidence to refuse such starts."
    )
