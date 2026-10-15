"""Opt-in start safeguards of a risk policy (ADR 0142, ADR 0143).

Each safeguard is a check ThyTrader always runs at a paper or live start (or resume) and
reports as a warning. Setting it here turns that warning into a refusal. Unset, or false, the
user's start goes ahead: safeguards inform, and the operator chooses which of them enforce.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_MIN_OUT_OF_SAMPLE_TRADES = 30
DEFAULT_MIN_PAPER_DAYS = 14


class LiveEvidenceThresholds(BaseModel):
    """What a live start's evidence must show not to be called thin (ADR 0143).

    The evidence is always recorded and a thin one always warns; these numbers only say
    where "thin" begins. Unset, the defaults apply.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    min_out_of_sample_trades: int = Field(
        default=DEFAULT_MIN_OUT_OF_SAMPLE_TRADES,
        ge=0,
        le=100_000,
        description=(
            "Trades the exact strategy fingerprint made across out-of-sample study windows."
        ),
    )
    require_positive_out_of_sample: bool = Field(
        default=True,
        description="Whether the out-of-sample windows' summed net result must be above zero.",
    )
    min_paper_days: int = Field(
        default=DEFAULT_MIN_PAPER_DAYS,
        ge=0,
        le=3650,
        description="Days since the exact fingerprint's first paper book was created.",
    )


class StartSafeguards(BaseModel):
    """Which start warnings refuse the start instead (all off by default)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    require_convergence_warmup: bool = Field(
        default=False,
        exclude_if=lambda value: value is False,
        description=(
            "Refuse to start or resume a strategy snapshot without the convergence warmup "
            "policy (WARMUP_POLICY_REQUIRED) instead of warning (ADR 0140)."
        ),
    )
    require_market_history: bool = Field(
        default=False,
        exclude_if=lambda value: value is False,
        description=(
            "Refuse a start whose market is younger than the strategy's warmup "
            "(HISTORY_TOO_SHORT) or whose history cannot be read (HISTORY_UNCONFIRMED) instead "
            "of warning; the bot otherwise waits until each window holds its warmup."
        ),
    )
    require_live_evidence: bool = Field(
        default=False,
        exclude_if=lambda value: value is False,
        description=(
            "Refuse a live start whose recorded evidence is thin (LIVE_EVIDENCE_REQUIRED) "
            "instead of warning with LIVE_EVIDENCE_THIN (ADR 0143)."
        ),
    )
    live_evidence: LiveEvidenceThresholds | None = Field(
        default=None,
        exclude_if=lambda value: value is None or value == LiveEvidenceThresholds(),
        description=(
            "Where thin live-start evidence begins; unset uses 30 out-of-sample trades, a "
            "positive out-of-sample net result and 14 paper days."
        ),
    )

    @property
    def live_evidence_thresholds(self) -> LiveEvidenceThresholds:
        """The thresholds in effect, the defaults when unset."""
        return self.live_evidence or LiveEvidenceThresholds()
