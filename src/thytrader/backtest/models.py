"""Canonical immutable outputs from the deterministic bar-level simulation engine."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from thytrader.research.models import (  # noqa: TC001
    BrokerAssumptions,
    FingerprintText,
    UtcDateTime,
)

_RESULT_DECIMAL_PATTERN = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")


def _validate_result_decimal_text(value: str) -> str:
    """Require one canonical finite result decimal within the engine's Decimal64 envelope."""
    if len(value) > 6211 or _RESULT_DECIMAL_PATTERN.fullmatch(value) is None:
        raise ValueError("result decimals must be canonical plain decimal strings")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("result decimals must be valid") from error
    exponent = parsed.as_tuple().exponent
    if (
        not parsed.is_finite()
        or not isinstance(exponent, int)
        or len(parsed.as_tuple().digits) > 64
        or exponent < -6206
        or parsed.adjusted() > 6144
    ):
        raise ValueError("result decimals must fit the deterministic Decimal envelope")
    if value != _canonical_result_decimal(parsed):
        raise ValueError("result decimals must use canonical plain decimal rendering")
    return value


def _canonical_result_decimal(value: Decimal) -> str:
    """Render a finite Decimal without ambient-context rounding or signed zero."""
    if value.is_zero():
        return "0"
    sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        raise TypeError("finite Decimal must have an integer exponent")
    coefficient = "".join(str(digit) for digit in digits)
    if exponent >= 0:
        text = coefficient + "0" * exponent
    else:
        point = len(coefficient) + exponent
        if point > 0:
            text = f"{coefficient[:point]}.{coefficient[point:]}"
        else:
            text = f"0.{('0' * -point)}{coefficient}"
        text = text.rstrip("0").rstrip(".")
    return f"-{'0' if text == '0' else text}" if sign else text


ResultDecimalText = Annotated[
    str,
    Field(strict=True, max_length=6211),
    AfterValidator(_validate_result_decimal_text),
]


class _FrozenBacktestModel(BaseModel):
    """Reject unknown fields and prevent mutation of simulation evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class BacktestFill(_FrozenBacktestModel):
    """One modeled immediate marketable fill using a completed-candle price assumption."""

    candle_starts_at: UtcDateTime
    price: ResultDecimalText
    quantity: ResultDecimalText
    notional: ResultDecimalText
    fee: ResultDecimalText
    fee_rate: ResultDecimalText
    reference_price: ResultDecimalText | None = None
    executable_side: Literal["ask", "bid", "mark"] | None = None
    spread_cost: ResultDecimalText | None = None

    @field_validator("candle_starts_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        """Require fills to reference timezone-aware UTC candle boundaries."""
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("candle_starts_at must be timezone-aware UTC")
        return value

    @field_serializer("candle_starts_at", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        """Render exact UTC timestamps with the canonical Z suffix."""
        return value.isoformat().replace("+00:00", "Z")


class BacktestExitFill(BacktestFill):
    """One modeled position-closing fill and its deterministic reason."""

    reason: Literal["stop_loss", "take_profit", "time_exit", "evaluation_end"]


class BacktestTrade(_FrozenBacktestModel):
    """One fully closed long-only modeled trade with exact accounting evidence."""

    entry: BacktestFill
    exit: BacktestExitFill
    gross_pnl: ResultDecimalText
    net_pnl: ResultDecimalText
    holding_bars: int = Field(ge=0)

    @model_validator(mode="after")
    def require_ordered_fills(self) -> Self:
        """Prevent impossible exits before their corresponding entry fill."""
        if self.exit.candle_starts_at < self.entry.candle_starts_at:
            raise ValueError("trade exit cannot precede entry")
        return self


class EquityPoint(_FrozenBacktestModel):
    """One mark-to-market account value after a deterministic candle transition."""

    candle_starts_at: UtcDateTime
    cash: ResultDecimalText
    base_quantity: ResultDecimalText
    mark_price: ResultDecimalText
    equity: ResultDecimalText

    @field_serializer("candle_starts_at", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        """Render exact UTC timestamps with the canonical Z suffix."""
        return value.isoformat().replace("+00:00", "Z")


class BacktestSummary(_FrozenBacktestModel):
    """Minimal deterministic metrics that do not imply unsupported statistical assumptions."""

    initial_equity: ResultDecimalText
    final_equity: ResultDecimalText
    total_net_pnl: ResultDecimalText
    total_return_fraction: ResultDecimalText
    gross_profit: ResultDecimalText
    gross_loss: ResultDecimalText
    win_rate: ResultDecimalText
    profit_factor: ResultDecimalText | None = None
    average_win: ResultDecimalText | None = None
    average_loss: ResultDecimalText | None = None
    trade_count: int = Field(ge=0)
    winning_trade_count: int = Field(ge=0)
    maximum_drawdown: ResultDecimalText
    maximum_drawdown_fraction: ResultDecimalText
    exposure_bars: int = Field(ge=0)
    evaluation_bars: int = Field(ge=1)
    total_spread_cost: ResultDecimalText | None = None

    @model_validator(mode="after")
    def require_wins_within_trade_count(self) -> Self:
        """Keep count metrics internally coherent."""
        if self.winning_trade_count > self.trade_count:
            raise ValueError("winning_trade_count cannot exceed trade_count")
        return self


class BacktestResult(_FrozenBacktestModel):
    """Canonical full result from one immutable research-run simulation."""

    schema_version: Literal["1.0"]
    engine_contract_version: Literal["thytrader-bar-backtest-v1", "thytrader-bar-backtest-v2"]
    broker: BrokerAssumptions | None = None
    run_fingerprint: FingerprintText
    strategy_fingerprint: FingerprintText
    dataset_fingerprint: FingerprintText
    signal_trace_fingerprint: FingerprintText
    trades: tuple[BacktestTrade, ...]
    equity_curve: tuple[EquityPoint, ...] = Field(min_length=1)
    summary: BacktestSummary

    @model_validator(mode="after")
    def require_v2_broker_evidence(self) -> Self:
        """Require V2 output to carry the same immutable broker facts as its source run."""
        is_v2 = self.engine_contract_version == "thytrader-bar-backtest-v2"
        if is_v2 and self.broker is None:
            raise ValueError("backtest V2 results require broker assumptions")
        if not is_v2 and self.broker is not None:
            raise ValueError("broker assumptions require a backtest V2 result")
        return self


class BacktestBenchmark(_FrozenBacktestModel):
    """Derived buy-and-hold comparison using the exact verified run and dataset contract."""

    benchmark_contract_version: Literal["thytrader-buy-and-hold-v1"]
    result_fingerprint: FingerprintText
    run_fingerprint: FingerprintText
    dataset_fingerprint: FingerprintText
    engine_contract_version: Literal["thytrader-bar-backtest-v1", "thytrader-bar-backtest-v2"]
    broker: BrokerAssumptions | None = None
    entry_candle_starts_at: UtcDateTime
    exit_candle_starts_at: UtcDateTime
    entry_price: ResultDecimalText
    exit_price: ResultDecimalText
    initial_equity: ResultDecimalText
    final_equity: ResultDecimalText
    total_net_pnl: ResultDecimalText
    total_return_fraction: ResultDecimalText
    total_fees: ResultDecimalText
    total_spread_cost: ResultDecimalText | None = None
    maximum_drawdown: ResultDecimalText
    maximum_drawdown_fraction: ResultDecimalText
    evaluation_bars: int = Field(ge=1)

    @field_validator("entry_candle_starts_at", "exit_candle_starts_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        """Require benchmark boundaries to be timezone-aware UTC timestamps."""
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("benchmark candle boundaries must be timezone-aware UTC")
        return value

    @field_serializer("entry_candle_starts_at", "exit_candle_starts_at", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        """Render benchmark boundaries with the canonical Z suffix."""
        return value.isoformat().replace("+00:00", "Z")

    @model_validator(mode="after")
    def require_v2_broker_evidence(self) -> Self:
        """Keep derived benchmark broker evidence aligned with its execution contract."""
        is_v2 = self.engine_contract_version == "thytrader-bar-backtest-v2"
        if is_v2 and self.broker is None:
            raise ValueError("benchmark V2 results require broker assumptions")
        if not is_v2 and self.broker is not None:
            raise ValueError("broker assumptions require a benchmark V2 result")
        return self


def canonical_backtest_result_bytes(result: BacktestResult) -> bytes:
    """Revalidate and encode a result as sorted compact canonical UTF-8 JSON."""
    validated = BacktestResult.model_validate(result.model_dump(mode="python"))
    return json.dumps(
        validated.model_dump(mode="json", exclude_none=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def backtest_result_fingerprint(result: BacktestResult) -> str:
    """Return the SHA-256 content identity of one canonical simulation result."""
    return f"sha256:{sha256(canonical_backtest_result_bytes(result)).hexdigest()}"
