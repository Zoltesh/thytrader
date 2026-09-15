"""Canonical V1/V2/V3 engine-support matrix for research and the operator UI."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EngineId = Literal[
    "thytrader-bar-backtest-v1",
    "thytrader-bar-backtest-v2",
    "thytrader-bar-backtest-v3",
]


class EngineSupportRow(BaseModel):
    """One setting and whether each bar-backtest engine consumes it."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    label: str
    v1: bool
    v2: bool
    v3: bool
    note: str


class EngineSupportMatrix(BaseModel):
    """Honest parallel-engine matrix. V3 does not retire V1 or V2."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: Literal["thytrader-engine-support-v1"] = "thytrader-engine-support-v1"
    engines: tuple[EngineId, ...] = (
        "thytrader-bar-backtest-v1",
        "thytrader-bar-backtest-v2",
        "thytrader-bar-backtest-v3",
    )
    rows: tuple[EngineSupportRow, ...] = Field(min_length=1)


def engine_support_matrix() -> EngineSupportMatrix:
    """Return the shipped V1/V2/V3 support matrix."""
    return EngineSupportMatrix(
        rows=(
            EngineSupportRow(
                label="HTF filter (optional closed-bar AND with LTF entry)",
                v1=True,
                v2=True,
                v3=True,
                note=(
                    "Research V1/V2/V3 evaluate last completed HTF bars only; "
                    "paper and live reject htf_filter"
                ),
            ),
            EngineSupportRow(
                label="Entry conditions (ALL / ANY / NOT, comparisons, crossovers)",
                v1=True,
                v2=True,
                v3=True,
                note="evaluated on completed candles, no lookahead",
            ),
            EngineSupportRow(
                label=(
                    "Indicators: EMA, SMA, RSI, ATR, volume SMA, highest, lowest, stdev, ROC, "
                    "Williams %R, CCI, WMA, momentum, MFI, MACD, Bollinger, "
                    "OHLCV identity, constant"
                ),
                v1=True,
                v2=True,
                v3=True,
                note=(
                    "exact Decimal arithmetic; paper/live share the LTF catalog; "
                    "HTF kinds only inside research htf_filter; "
                    "MACD/Bollinger conditions use series ids"
                ),
            ),
            EngineSupportRow(
                label="Per-indicator timeframes",
                v1=False,
                v2=False,
                v3=False,
                note="out of Phase 9; LTF uses top-level timeframe, HTF stays inside htf_filter",
            ),
            EngineSupportRow(
                label="Risk-fraction sizing with notional bounds",
                v1=True,
                v2=True,
                v3=True,
                note="bounded by exposure fraction",
            ),
            EngineSupportRow(
                label="ATR initial stop",
                v1=True,
                v2=True,
                v3=True,
                note="stop-loss priority inside the bar",
            ),
            EngineSupportRow(
                label="Reward/risk take profit",
                v1=True,
                v2=True,
                v3=True,
                note="V1/V2 check after the stop; V3 rests take-profit after the fill bar",
            ),
            EngineSupportRow(
                label="Time exit (max bars held)",
                v1=True,
                v2=True,
                v3=True,
                note="V1/V2 exit at the open; V3 exits at the completed close",
            ),
            EngineSupportRow(
                label="Constant spread stress assumption",
                v1=False,
                v2=True,
                v3=False,
                note="V2 models an explicit total bid-ask spread; V1 and V3 do not",
            ),
            EngineSupportRow(
                label="Entry cooldown (cooldown_bars)",
                v1=False,
                v2=False,
                v3=False,
                note="not modeled by V1, V2, or V3 bar backtesters",
            ),
            EngineSupportRow(
                label="Maker-only / marketable entry preference",
                v1=False,
                v2=False,
                v3=True,
                note="V1/V2 fill at next open; V3 rests a post-only close limit",
            ),
            EngineSupportRow(
                label="Entry wait and unfilled policy",
                v1=False,
                v2=False,
                v3=True,
                note="V3 honors max_entry_wait_bars and on_unfilled_entry cancel/reprice",
            ),
            EngineSupportRow(
                label="Trailing stop",
                v1=False,
                v2=False,
                v3=False,
                note="the published strategy profile permits disabled only",
            ),
            EngineSupportRow(
                label="Walk-forward / OOS / cross-market studies",
                v1=True,
                v2=True,
                v3=True,
                note=(
                    "Phase 11 composes existing engines; it does not retune parameters or "
                    "stitch a continuous equity curve"
                ),
            ),
        )
    )
