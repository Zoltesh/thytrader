"""Canonical V1-V4 engine-support matrix for research and the operator UI."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EngineId = Literal[
    "thytrader-bar-backtest-v1",
    "thytrader-bar-backtest-v2",
    "thytrader-bar-backtest-v3",
    "thytrader-bar-backtest-v4",
]


class EngineSupportRow(BaseModel):
    """One setting and whether each bar-backtest engine consumes it."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    label: str
    v1: bool
    v2: bool
    v3: bool
    v4: bool
    note: str


class EngineSupportMatrix(BaseModel):
    """Honest parallel-engine matrix. Later engines do not retire earlier fingerprints."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: Literal["thytrader-engine-support-v2"] = "thytrader-engine-support-v2"
    engines: tuple[EngineId, ...] = (
        "thytrader-bar-backtest-v1",
        "thytrader-bar-backtest-v2",
        "thytrader-bar-backtest-v3",
        "thytrader-bar-backtest-v4",
    )
    rows: tuple[EngineSupportRow, ...] = Field(min_length=1)


def engine_support_matrix() -> EngineSupportMatrix:
    """Return the shipped V1-V4 support matrix."""
    return EngineSupportMatrix(
        rows=(
            EngineSupportRow(
                label="HTF filter (optional closed-bar AND with LTF entry)",
                v1=True,
                v2=True,
                v3=True,
                v4=True,
                note=("Research engines and paper/live evaluate last completed HTF bars only"),
            ),
            EngineSupportRow(
                label="Entry conditions (ALL / ANY / NOT, comparisons, crossovers)",
                v1=True,
                v2=True,
                v3=True,
                v4=True,
                note="evaluated on completed candles, no lookahead",
            ),
            EngineSupportRow(
                label=(
                    "Indicators: EMA, SMA, RSI, ATR, volume SMA, highest, lowest, stdev, "
                    "sample stdev, ROC, Williams %R, CCI, WMA, momentum, MFI, MACD, "
                    "Bollinger, stochastic, ADX, OHLCV identity, constant"
                ),
                v1=True,
                v2=True,
                v3=True,
                v4=True,
                note=(
                    "exact Decimal arithmetic; paper/live share the LTF catalog and "
                    "evaluate HTF kinds inside htf_filter; "
                    "MACD/Bollinger/stochastic/ADX conditions use series ids; "
                    "rolling EMA/SMA/WMA/highest/lowest/stdev/sample-stdev/ROC/"
                    "momentum accept one OHLCV field"
                ),
            ),
            EngineSupportRow(
                label="Per-indicator timeframes",
                v1=True,
                v2=True,
                v3=True,
                v4=True,
                note=(
                    "optional LTF-list timeframe uses last-completed extra-TF bars; "
                    "paper/live compose with HTF; no interpolation"
                ),
            ),
            EngineSupportRow(
                label="Risk-fraction sizing with notional bounds",
                v1=True,
                v2=True,
                v3=True,
                v4=True,
                note="bounded by exposure fraction",
            ),
            EngineSupportRow(
                label="ATR initial stop",
                v1=True,
                v2=True,
                v3=True,
                v4=True,
                note="stop-loss priority inside the bar",
            ),
            EngineSupportRow(
                label="Reward/risk take profit",
                v1=True,
                v2=True,
                v3=True,
                v4=True,
                note="V1/V2 check after the stop; V3/V4 rest take-profit after the fill bar",
            ),
            EngineSupportRow(
                label="Time exit (max bars held)",
                v1=True,
                v2=True,
                v3=True,
                v4=True,
                note="V1/V2 exit at the open; V3 exits at close; V4 uses causal terminal rules",
            ),
            EngineSupportRow(
                label="Constant spread stress assumption",
                v1=False,
                v2=True,
                v3=False,
                v4=False,
                note="V2 models an explicit total bid-ask spread; V1 and V3/V4 maker paths do not",
            ),
            EngineSupportRow(
                label="Entry cooldown (cooldown_bars)",
                v1=False,
                v2=False,
                v3=True,
                v4=True,
                note=(
                    "V3/V4 maker path blocks re-entry for cooldown_bars after exit or "
                    "unfilled cancel; V1/V2 do not model cooldown"
                ),
            ),
            EngineSupportRow(
                label="Maker-only / marketable entry preference",
                v1=False,
                v2=False,
                v3=True,
                v4=True,
                note="V1/V2 fill at next open; V3/V4 rest a post-only close limit",
            ),
            EngineSupportRow(
                label="Entry wait and unfilled policy",
                v1=False,
                v2=False,
                v3=True,
                v4=True,
                note="V3/V4 honor max_entry_wait_bars and on_unfilled_entry cancel/reprice",
            ),
            EngineSupportRow(
                label="Causal evaluation terminal",
                v1=True,
                v2=True,
                v3=False,
                v4=True,
                note=(
                    "V4 liquidates at evaluation.ends_at open without post-boundary intrabar logic"
                ),
            ),
            EngineSupportRow(
                label="Taker slippage on stop/time/end exits",
                v1=True,
                v2=True,
                v3=False,
                v4=True,
                note=(
                    "V4 honors fixed_slippage_bps on taker liquidations; "
                    "V3 maker TP stays zero slippage"
                ),
            ),
            EngineSupportRow(
                label="Validity limits on summaries",
                v1=False,
                v2=False,
                v3=False,
                v4=True,
                note="documents maker touch-fill, TP-before-stop, and spot-short modeling limits",
            ),
            EngineSupportRow(
                label="Trailing stop",
                v1=False,
                v2=False,
                v3=True,
                v4=True,
                note=(
                    "enabled ATR-multiple trailing shares the paper/live ratchet on V3/V4; "
                    "V4 applies causal same-bar ordering per ADR 0062; disabled is a no-op"
                ),
            ),
            EngineSupportRow(
                label="Walk-forward / OOS / cross-market studies",
                v1=True,
                v2=True,
                v3=True,
                v4=True,
                note=(
                    "Phase 11 and ADR 0044 compose existing engines; prefer V4 for selection "
                    "after ADR 0062"
                ),
            ),
        )
    )
