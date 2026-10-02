"""Shared fixtures for read-only reference-instrument tests (ADR 0096).

The fixture strategy trades ETH-USD on 1h and enters only while BTC-USD's last closed
daily close is above its 2-day SMA, so every entry outcome is decided by the
reference instrument alone. Daily reference bars and hourly decision bars are built
from plain decimal closes with valid OHLC geometry.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
from typing import cast
from uuid import UUID

from thytrader.market_data.models import Candle
from thytrader.research.models import (
    CapitalAssumptions,
    CostAssumptions,
    EvaluationWindow,
    ReferenceInstrumentDataset,
    ResearchRunSpecification,
    WarmupWindow,
)
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint

GOLDEN = Path(__file__).parent / "golden"
DECISION_DATASET = "sha256:" + "d" * 64
REFERENCE_DATASET = "sha256:" + "e" * 64
OHLCV = ["open", "high", "low", "close", "volume"]


def reference_payload(
    *,
    product_id: str = "ETH-USD",
    timeframe: str = "1h",
    reference_product: str = "BTC-USD",
    reference_timeframe: str = "1d",
) -> dict[str, object]:
    """Return a mutable document whose entry reads only the BTC reference instrument."""
    payload = cast(
        "dict[str, object]",
        json.loads((GOLDEN / "reference_strategy_v1.json").read_text(encoding="utf-8")),
    )
    base, quote = product_id.split("-")
    payload["instrument"] = {
        "product_id": product_id,
        "base_currency": base,
        "quote_currency": quote,
    }
    payload["timeframe"] = timeframe
    payload["data_requirements"] = {
        "warmup_bars": 2,
        "required_fields": OHLCV,
        "reference_instruments": [
            {"id": "btc", "product_id": reference_product, "timeframe": reference_timeframe}
        ],
    }
    payload["indicators"] = [
        {
            "id": "atr",
            "kind": "atr",
            "input": ["high", "low", "close"],
            "parameters": {"period": 2},
        },
        {
            "id": "btc_close",
            "kind": "identity",
            "input": "close",
            "parameters": {},
            "source": "btc",
        },
        {
            "id": "btc_sma",
            "kind": "sma",
            "input": "close",
            "parameters": {"period": 2},
            "source": "btc",
        },
    ]
    payload["entry"] = {
        "side": "long",
        "when": {
            "all": [
                {
                    "left": {"indicator": "btc_close"},
                    "operator": "greater_than",
                    "right": {"indicator": "btc_sma"},
                }
            ]
        },
        "cooldown_bars": 0,
        "max_open_positions": 1,
    }
    payload["exits"] = {
        "initial_stop": {"kind": "atr_multiple", "atr_indicator": "atr", "multiple": "2"},
        "take_profit": {"kind": "reward_risk", "multiple": "2"},
        "trailing_stop": {"enabled": False},
        "time_exit": {"max_bars_held": 96},
    }
    return payload


def reference_strategy(**overrides: str) -> StrategyDefinition:
    """Validate :func:`reference_payload` with optional product/timeframe overrides."""
    return StrategyDefinition.model_validate(reference_payload(**overrides))


def candle(starts_at: datetime, close: str) -> Candle:
    """Build one geometrically valid OHLCV bar closing at ``close``."""
    value = Decimal(close)
    return Candle(
        starts_at=starts_at,
        open=value,
        high=value + Decimal("1"),
        low=value - Decimal("0.5"),
        close=value,
        volume=Decimal("10"),
    )


def daily_bars(first_day: datetime, *closes: str) -> tuple[Candle, ...]:
    """Consecutive 1d bars starting at ``first_day`` (UTC midnight)."""
    return tuple(
        candle(first_day + timedelta(days=index), close) for index, close in enumerate(closes)
    )


def hourly_bars(first_hour: datetime, count: int, close: str = "100") -> tuple[Candle, ...]:
    """``count`` consecutive flat 1h bars starting at ``first_hour``."""
    return tuple(candle(first_hour + timedelta(hours=index), close) for index in range(count))


def reference_binding(
    strategy: StrategyDefinition, fingerprint: str = REFERENCE_DATASET
) -> ReferenceInstrumentDataset:
    """Bind the fixture's single ``btc`` reference to one dataset fingerprint."""
    reference = strategy.data_requirements.reference_instruments[0]
    return ReferenceInstrumentDataset(
        reference_id=reference.id,
        product_id=reference.product_id,
        timeframe=reference.timeframe,
        dataset_fingerprint=fingerprint,
    )


def reference_run(
    strategy: StrategyDefinition,
    *,
    starts_at: datetime,
    hours: int,
    bound: bool = True,
) -> ResearchRunSpecification:
    """An hourly research run over ``hours`` evaluation bars with the reference bound."""
    return ResearchRunSpecification(
        schema_version="1.0",
        run_id=UUID("019faf76-6600-7000-8000-000000000095"),
        created_at=datetime(2026, 7, 29, 20, tzinfo=UTC),
        strategy_fingerprint=strategy_fingerprint(strategy),
        dataset_fingerprint=DECISION_DATASET,
        reference_dataset_fingerprints=(reference_binding(strategy),) if bound else (),
        evaluation=EvaluationWindow(
            starts_at=starts_at, ends_at=starts_at + timedelta(hours=hours)
        ),
        warmup=WarmupWindow(
            bars=strategy.data_requirements.warmup_bars,
            starts_at=starts_at - timedelta(hours=strategy.data_requirements.warmup_bars),
        ),
        capital=CapitalAssumptions(quote_currency="USD", initial_quote_balance="10000"),
        costs=CostAssumptions(
            maker_fee_rate="0.001", taker_fee_rate="0.002", fixed_slippage_bps="10"
        ),
        random_seed=0,
    )
