"""Opt-in start safeguards of a risk policy (ADR 0142).

Each safeguard is a check ThyTrader always runs at a paper or live start (or resume) and
reports as a warning. Setting it here turns that warning into a refusal. Unset, or false, the
user's start goes ahead: safeguards inform, and the operator chooses which of them enforce.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


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
