"""Plain-language description of the single unified backtest model (ADR 0083).

Agents read this through ``GET /api/v1/research/backtest-model`` and
``thytrader-research backtest-model``; the browser renders the same assumptions as its
"How backtests simulate" disclosure. It grants no trading authority.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from thytrader.evaluation.models import BACKTEST_ENGINE, BacktestEngine

BacktestAssumptionKey = Literal[
    "signal_timing",
    "maker_entries",
    "unfilled_entries",
    "stops_and_targets",
    "time_exit",
    "signal_exit",
    "evaluation_end",
    "fees",
    "slippage",
    "spread_stress",
    "shorts",
    "multi_instrument",
    "pyramiding",
    "queue_position",
    "no_trade_bars",
]


class BacktestModelAssumption(BaseModel):
    """One fill or cost assumption every backtest result is simulated under."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    key: BacktestAssumptionKey
    label: str
    detail: str


class BacktestModelDescription(BaseModel):
    """The one backtest model's identity, honest scope, and fill assumptions."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    engine: BacktestEngine = BACKTEST_ENGINE
    decision_record: Literal["ADR 0083"] = "ADR 0083"
    honesty: str
    assumptions: tuple[BacktestModelAssumption, ...] = Field(min_length=1)


def backtest_model_description() -> BacktestModelDescription:
    """Return the shipped description of the unified backtest model."""
    return BacktestModelDescription(
        honesty=(
            "Backtests are simulated results from historical candles: research evidence, "
            "not a promise of paper or live performance."
        ),
        assumptions=(
            BacktestModelAssumption(
                key="signal_timing",
                label="Signals on completed candles",
                detail=(
                    "Entry conditions, HTF filters, and per-indicator timeframes are evaluated "
                    "only on completed candles; no future bar influences a signal."
                ),
            ),
            BacktestModelAssumption(
                key="maker_entries",
                label="Maker-limit entries rest",
                detail=(
                    "A matched signal rests a post-only limit at that candle's close. A later "
                    "candle fills it only if its low (high for shorts) trades through the "
                    "limit, at the limit price with the maker fee and no slippage."
                ),
            ),
            BacktestModelAssumption(
                key="unfilled_entries",
                label="Unfilled entries expire",
                detail=(
                    "An entry rests up to max_entry_wait_bars candles, then cancels (with at "
                    "least one bar of cooldown) or reprices at that candle's close."
                ),
            ),
            BacktestModelAssumption(
                key="stops_and_targets",
                label="Stops and targets on bar extremes",
                detail=(
                    "On the fill candle only the stop can trigger (stop first); the "
                    "take-profit rests from the next candle. On every candle the stop is "
                    "checked first: a candle touching both the stop and the take-profit is "
                    "resolved as the stop, because a candle cannot show which traded first "
                    "(paper does the same). Otherwise a touched take-profit fills at the "
                    "target. Stops fill as takers at the stop or the worse open on a gap; ATR "
                    "trailing ratchets after the check."
                ),
            ),
            BacktestModelAssumption(
                key="time_exit",
                label="Time exit at close",
                detail="A position held max_bars_held candles sells at that candle's close.",
            ),
            BacktestModelAssumption(
                key="signal_exit",
                label="Signal exit at close",
                detail=(
                    "With exits.signal_exit, a position sells as a taker at the close of the "
                    "first completed candle after the fill candle whose exit rule matches, "
                    "like the time exit (taker fee, slippage, half the spread stress). A stop "
                    "or take-profit that the same candle touched wins; the signal exit "
                    "precedes the time exit. Results disclose signal_exit_at_close."
                ),
            ),
            BacktestModelAssumption(
                key="evaluation_end",
                label="Liquidation at the window end",
                detail=(
                    "Open inventory is sold at the open of the candle at evaluation end; "
                    "nothing else is processed on that candle."
                ),
            ),
            BacktestModelAssumption(
                key="fees",
                label="Maker and taker fees",
                detail=(
                    "Resting entries and take-profits pay maker_fee_rate; stop, time, signal, "
                    "and end-of-window exits pay taker_fee_rate. Rates are modeled inputs."
                ),
            ),
            BacktestModelAssumption(
                key="slippage",
                label="Fixed taker slippage",
                detail="fixed_slippage_bps moves every taker exit against the position.",
            ),
            BacktestModelAssumption(
                key="spread_stress",
                label="Optional spread stress",
                detail=(
                    "spread_bps (default 0) is a constant total bid-ask spread stress. Taker "
                    "exits cross half of it before slippage, and stop triggers and open-position "
                    "marks use the stressed bid (ask for shorts). Maker fills stay at their "
                    "limit. It is a stress input, not observed order-book data."
                ),
            ),
            BacktestModelAssumption(
                key="shorts",
                label="Spot shorts are synthetic",
                detail=(
                    "Short strategies sell to open and buy to cover against quote cash; there "
                    "is no borrow, margin, or funding model."
                ),
            ),
            BacktestModelAssumption(
                key="multi_instrument",
                label="One shared quote balance",
                detail=(
                    "Multi-instrument strategies process products in product_id order on each "
                    "candle against one quote balance, capped by max_concurrent_positions."
                ),
            ),
            BacktestModelAssumption(
                key="pyramiding",
                label="Same-side adds",
                detail=(
                    "Pyramiding adds rest as maker limits and average into the open position "
                    "without moving its stop or target."
                ),
            ),
            BacktestModelAssumption(
                key="queue_position",
                label="Candles don't show queue position",
                detail=(
                    "A touched limit is assumed to fill completely. Real resting orders can "
                    "miss or partially fill when the price only touches them. Optional "
                    "execution_stress "
                    "adds deterministic activation latency, maker penetration, and a partial entry "
                    "whose remainder is canceled. It is fingerprinted in costs and is not "
                    "observed queue data."
                ),
            ),
            BacktestModelAssumption(
                key="no_trade_bars",
                label="No-trade bars are flat",
                detail=(
                    "Coinbase reports no candle for an interval without trades. Datasets "
                    "fill each confirmed no-trade interval with a flat zero-volume bar at "
                    "the previous close (ADR 0095), and signals, fills, and stops evaluate it "
                    "like any bar, as paper and live do. Results whose window contains such "
                    "bars disclose synthetic_no_trade_bars."
                ),
            ),
        ),
    )
