"""Golden, reference, warmup, lag, and edge-case tests for the wider indicator catalog.

Every new kind is checked three ways: against an independent Decimal reference
(``tests/research/indicator_reference.py``), against TA-Lib 0.8.1 values recorded on the
same deterministic series where TA-Lib implements the formula, and against documented
edge-case conventions (flat prices, zero ranges, zero volume). Generic properties cover
all 53 kinds: first fully defined bar equals ``warmup - 1``, a prefix never changes
earlier values (no lookahead), and ``offset`` is an exact shift.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, cast
from uuid import UUID

from pydantic import ValidationError
import pytest

from tests.evaluation.indicator_reference import (
    TOLERANCE,
    close_enough,
    flat_candles,
    in_engine_context,
    ref_accumulation_distribution,
    ref_aroon,
    ref_awesome_oscillator,
    ref_bollinger,
    ref_choppiness,
    ref_cmf,
    ref_cmo,
    ref_dema,
    ref_force_index,
    ref_historical_volatility,
    ref_hma,
    ref_kama,
    ref_keltner,
    ref_linear_regression,
    ref_midpoint,
    ref_natr,
    ref_obv,
    ref_parabolic_sar,
    ref_percent_rank,
    ref_ppo,
    ref_sma,
    ref_stochastic_rsi,
    ref_supertrend,
    ref_tema,
    ref_trix,
    ref_tsi,
    ref_ultimate_oscillator,
    ref_vortex,
    ref_vwap,
    ref_vwma,
    ref_zscore,
    synthetic_candles,
)
from thytrader.evaluation.indicators import calculate_indicator_rows
from thytrader.evaluation.models import (
    CapitalAssumptions,
    CostAssumptions,
    EvaluationWindow,
    ReferenceInstrumentDataset,
    ResearchRunSpecification,
    WarmupWindow,
)
from thytrader.evaluation.signal_evaluator import evaluate_signal_trace
from thytrader.evaluation.trace import EntryConditionOutcome
from thytrader.execution.signals import evaluate_latest_entry
from thytrader.market_data.models import Candle
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.indicator_catalog import (
    INDICATOR_KIND_SPECS,
    default_indicator_definition,
)
from thytrader.strategies.models import (
    IndicatorDefinition,
    IndicatorKind,
    StrategyDefinition,
    indicator_min_warmup,
    indicator_value_keys,
    reference_instruments,
    strategy_fingerprint,
)
from thytrader.strategies.template_ids import StrategyTemplateId

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tests.evaluation.indicator_reference import Series
    from thytrader.strategies.indicator_spec_model import IndicatorKindSpec

CANDLES = synthetic_candles(240)
CLOSES = [candle.close for candle in CANDLES]
_START = datetime(2026, 1, 1, tzinfo=UTC)
_DAILY = tuple(
    replace(candle, starts_at=_START - timedelta(days=120) + timedelta(days=index))
    for index, candle in enumerate(synthetic_candles(132, seed=95))
)
"""Synthetic daily reference bars from 2025-09-03 through 2026-01-12 (ADR 0096)."""

DECLARATIONS: dict[str, dict[str, object]] = {
    "dema": {"kind": "dema", "input": "close", "parameters": {"period": 10}},
    "tema": {"kind": "tema", "input": "close", "parameters": {"period": 10}},
    "hma": {"kind": "hma", "input": "close", "parameters": {"period": 16}},
    "hma9": {"kind": "hma", "input": "close", "parameters": {"period": 9}},
    "kama": {
        "kind": "kama",
        "input": "close",
        "parameters": {"period": 10, "fast_period": 2, "slow_period": 30},
    },
    "vwma": {"kind": "vwma", "input": ["close", "volume"], "parameters": {"period": 14}},
    "st": {
        "kind": "supertrend",
        "input": ["high", "low", "close"],
        "parameters": {"period": 10, "multiplier": "3"},
    },
    "sar": {
        "kind": "parabolic_sar",
        "input": ["high", "low"],
        "parameters": {"step": "0.02", "max_step": "0.2"},
    },
    "aroon": {"kind": "aroon", "input": ["high", "low"], "parameters": {"period": 14}},
    "ichi": {
        "kind": "ichimoku",
        "input": ["high", "low"],
        "parameters": {"tenkan_period": 9, "kijun_period": 26, "senkou_b_period": 52},
    },
    "vortex": {"kind": "vortex", "input": ["high", "low", "close"], "parameters": {"period": 14}},
    "lr": {"kind": "linear_regression", "input": "close", "parameters": {"period": 14}},
    "trix": {"kind": "trix", "input": "close", "parameters": {"period": 10}},
    "srsi": {
        "kind": "stochastic_rsi",
        "input": "close",
        "parameters": {"rsi_period": 14, "stoch_period": 14, "k_period": 1, "d_period": 3},
    },
    "srsi3": {
        "kind": "stochastic_rsi",
        "input": "close",
        "parameters": {"rsi_period": 14, "stoch_period": 14, "k_period": 3, "d_period": 3},
    },
    "ppo": {
        "kind": "ppo",
        "input": "close",
        "parameters": {"fast_period": 12, "slow_period": 26, "signal_period": 9},
    },
    "uo": {
        "kind": "ultimate_oscillator",
        "input": ["high", "low", "close"],
        "parameters": {"short_period": 7, "medium_period": 14, "long_period": 28},
    },
    "ao": {
        "kind": "awesome_oscillator",
        "input": ["high", "low"],
        "parameters": {"fast_period": 5, "slow_period": 34},
    },
    "cmo": {"kind": "cmo", "input": "close", "parameters": {"period": 14}},
    "tsi": {
        "kind": "tsi",
        "input": "close",
        "parameters": {"long_period": 25, "short_period": 13, "signal_period": 13},
    },
    "kc": {
        "kind": "keltner",
        "input": ["high", "low", "close"],
        "parameters": {"period": 20, "atr_period": 10, "multiplier": "2"},
    },
    "dc": {"kind": "donchian", "input": ["high", "low"], "parameters": {"period": 20}},
    "pb": {
        "kind": "bollinger_percent_b",
        "input": "close",
        "parameters": {"period": 20, "stdev_multiplier": "2"},
    },
    "bw": {
        "kind": "bollinger_bandwidth",
        "input": "close",
        "parameters": {"period": 20, "stdev_multiplier": "2"},
    },
    "natr": {"kind": "natr", "input": ["high", "low", "close"], "parameters": {"period": 14}},
    "chop": {
        "kind": "choppiness",
        "input": ["high", "low", "close"],
        "parameters": {"period": 14},
    },
    "hv": {"kind": "historical_volatility", "input": "close", "parameters": {"period": 20}},
    "hva": {
        "kind": "historical_volatility",
        "input": "close",
        "parameters": {"period": 20, "annualization_periods": 365},
    },
    "obv": {"kind": "obv", "input": ["close", "volume"], "parameters": {"signal_period": 20}},
    "cmf": {
        "kind": "cmf",
        "input": ["high", "low", "close", "volume"],
        "parameters": {"period": 20},
    },
    "ad": {
        "kind": "accumulation_distribution",
        "input": ["high", "low", "close", "volume"],
        "parameters": {"signal_period": 20},
    },
    "vwap": {
        "kind": "vwap",
        "input": ["high", "low", "close", "volume"],
        "parameters": {"period": 14},
    },
    "fi": {"kind": "force_index", "input": ["close", "volume"], "parameters": {"period": 13}},
    "z": {"kind": "zscore", "input": "close", "parameters": {"period": 20}},
    "pr": {"kind": "percent_rank", "input": "close", "parameters": {"period": 20}},
}


def _definitions(names: Sequence[str]) -> list[IndicatorDefinition]:
    """Validate the named declarations with their dictionary key as id."""
    return [
        IndicatorDefinition.model_validate({"id": name, **DECLARATIONS[name]}) for name in names
    ]


def _engine(
    candles: Sequence[Candle] = CANDLES,
) -> tuple[dict[str, Decimal | None], ...]:
    """Compute every declared kind once over the shared synthetic series."""
    return calculate_indicator_rows(_definitions(tuple(DECLARATIONS)), candles)


ENGINE_ROWS = _engine()


def _column(rows: Sequence[dict[str, Decimal | None]], key: str) -> list[Decimal | None]:
    """Return one output key across all rows."""
    return [row[key] for row in rows]


def _reference_series() -> dict[str, Series]:
    """Compute every reference series under the engine context."""

    def build() -> dict[str, Series]:
        """Return reference values keyed like the engine rows."""
        supertrend_value, supertrend_direction = ref_supertrend(CANDLES, 10, Decimal(3))
        aroon_up, aroon_down, aroon_oscillator = ref_aroon(CANDLES, 14)
        vortex_plus, vortex_minus = ref_vortex(CANDLES, 14)
        line_value, line_slope = ref_linear_regression(CLOSES, 14)
        srsi_k, srsi_d = ref_stochastic_rsi(CLOSES, 14, 14, 1, 3)
        srsi3_k, srsi3_d = ref_stochastic_rsi(CLOSES, 14, 14, 3, 3)
        ppo_line, ppo_signal, ppo_histogram = ref_ppo(CLOSES, 12, 26, 9)
        tsi_line, tsi_signal = ref_tsi(CLOSES, 25, 13, 13)
        kc_upper, kc_middle, kc_lower = ref_keltner(CANDLES, 20, 10, Decimal(2))
        bb_middle, bb_upper, bb_lower = ref_bollinger(CLOSES, 20, Decimal(2))
        tenkan = ref_midpoint(CANDLES, 9)
        kijun = ref_midpoint(CANDLES, 26)
        obv = ref_obv(CANDLES)
        ad = ref_accumulation_distribution(CANDLES)
        dc_upper = [
            None if index < 19 else max(c.high for c in CANDLES[index - 19 : index + 1])
            for index in range(len(CANDLES))
        ]
        dc_lower = [
            None if index < 19 else min(c.low for c in CANDLES[index - 19 : index + 1])
            for index in range(len(CANDLES))
        ]
        return {
            "dema": ref_dema(CLOSES, 10),
            "tema": ref_tema(CLOSES, 10),
            "hma": ref_hma(CLOSES, 16),
            "hma9": ref_hma(CLOSES, 9),
            "kama": ref_kama(CLOSES, 10, 2, 30),
            "vwma": ref_vwma(CANDLES, 14),
            "st.value": supertrend_value,
            "st.direction": supertrend_direction,
            "sar": ref_parabolic_sar(CANDLES, Decimal("0.02"), Decimal("0.2")),
            "aroon.up": aroon_up,
            "aroon.down": aroon_down,
            "aroon.oscillator": aroon_oscillator,
            "ichi.tenkan": tenkan,
            "ichi.kijun": kijun,
            "ichi.senkou_a": [
                None if t is None or k is None else (t + k) / Decimal(2)
                for t, k in zip(tenkan, kijun, strict=True)
            ],
            "ichi.senkou_b": ref_midpoint(CANDLES, 52),
            "vortex.plus": vortex_plus,
            "vortex.minus": vortex_minus,
            "lr.value": line_value,
            "lr.slope": line_slope,
            "trix": ref_trix(CLOSES, 10),
            "srsi.k": srsi_k,
            "srsi.d": srsi_d,
            "srsi3.k": srsi3_k,
            "srsi3.d": srsi3_d,
            "ppo.ppo": ppo_line,
            "ppo.signal": ppo_signal,
            "ppo.histogram": ppo_histogram,
            "uo": ref_ultimate_oscillator(CANDLES, 7, 14, 28),
            "ao": ref_awesome_oscillator(CANDLES, 5, 34),
            "cmo": ref_cmo(CLOSES, 14),
            "tsi.tsi": tsi_line,
            "tsi.signal": tsi_signal,
            "kc.upper": kc_upper,
            "kc.middle": kc_middle,
            "kc.lower": kc_lower,
            "dc.upper": dc_upper,
            "dc.lower": dc_lower,
            "dc.middle": [
                None if u is None or lo is None else (u + lo) / Decimal(2)
                for u, lo in zip(dc_upper, dc_lower, strict=True)
            ],
            "pb": [
                None if u is None or lo is None else (close - lo) / (u - lo)
                for close, u, lo in zip(CLOSES, bb_upper, bb_lower, strict=True)
            ],
            "bw": [
                None if m is None or u is None or lo is None else (u - lo) / m
                for m, u, lo in zip(bb_middle, bb_upper, bb_lower, strict=True)
            ],
            "natr": ref_natr(CANDLES, 14),
            "chop": ref_choppiness(CANDLES, 14),
            "hv": ref_historical_volatility(CLOSES, 20, None),
            "hva": ref_historical_volatility(CLOSES, 20, 365),
            "obv.obv": obv,
            "obv.signal": ref_sma(obv, 20),
            "cmf": ref_cmf(CANDLES, 20),
            "ad.ad": ad,
            "ad.signal": ref_sma(ad, 20),
            "vwap": ref_vwap(CANDLES, 14),
            "fi": ref_force_index(CANDLES, 13),
            "z": ref_zscore(CLOSES, 20),
            "pr": ref_percent_rank(CLOSES, 20),
        }

    return in_engine_context(build)


REFERENCE = _reference_series()

# TA-Lib 0.8.1 (float64) on the same 240-bar series, recorded once; see the module
# docstring. Where TA-Lib defines a formula the engine follows (DEMA, TEMA, KAMA, SAR,
# AROON, MIDPRICE, LINEARREG, TRIX, STOCHRSI, PPO with EMA, ULTOSC, AD, BBANDS, OBV,
# MAX/MIN, EMA) the engine agrees to float precision. OBV is shifted by TA-Lib's
# first-bar volume seed (the engine starts at 0); Stochastic RSI uses k_period=1.
TALIB_GOLDEN: dict[str, dict[int, float]] = {
    "dema": {80: 112.27932209996182, 160: 104.80279584856102, 239: 104.67020001563134},
    "tema": {80: 113.2048069810771, 160: 104.35491586135727, 239: 104.7329441217755},
    "kama": {80: 110.57607714768366, 160: 104.37157859169285, 239: 104.07407686489371},
    "sar": {80: 109.08332, 160: 107.879870752, 239: 107.06524927999999},
    "aroon.up": {80: 100.0, 160: 50.0, 239: 42.85714285714286},
    "aroon.down": {80: 0.0, 160: 0.0, 239: 0.0},
    "aroon.oscillator": {80: 100.0, 160: 50.0, 239: 42.85714285714286},
    "ichi.tenkan": {80: 110.00229999999999, 160: 105.55715000000001, 239: 105.0714},
    "ichi.kijun": {80: 107.58395, 160: 103.92699999999999, 239: 104.03565},
    "ichi.senkou_b": {80: 106.8107, 160: 103.92699999999999, 239: 103.59615},
    "lr.value": {80: 111.76171142857055, 160: 104.85595999999761, 239: 104.51663714285263},
    "lr.slope": {80: 0.5180380219778775, 160: -0.064200659341033, 239: -0.029215164835869264},
    "trix": {80: 0.3189841901803492, 160: 0.10400170967057587, 239: 0.01829976943381606},
    "srsi.k": {80: 100.0, 160: 17.669082344509025, 239: 72.58037327193563},
    "srsi.d": {80: 99.99999999999999, 160: 25.988850759750807, 239: 48.78805226842868},
    "ppo.ppo": {80: 1.9422405294298482, 160: 0.32922862121519986, 239: 0.1316416772523035},
    "uo": {80: 78.96699222314079, 160: 39.10391518941126, 239: 51.97086721908646},
    "ad.ad": {80: 296.3997254872298, 160: 7.382397035471115, 239: 1946.2586369676424},
    "pb": {80: 1.0950552825994293, 160: 0.4449706655233761, 239: 0.8072810984351904},
    "bw": {80: 0.1005376563086497, 160: 0.06414076732842251, 239: 0.05710199300357904},
    "obv.obv": {80: 1576.7000000000007, 160: -2812.499999999998, 239: -504.5999999999981},
    "ao": {80: 5.651635588235266, 160: 0.5259808823528971, 239: -0.1391344117647435},
    "dc.upper": {80: 114.0787, 160: 108.429, 239: 107.332},
    "dc.lower": {80: 102.0605, 160: 100.4324, 239: 100.6933},
    "kc.middle": {80: 107.92930456299808, 160: 104.51474669592487, 239: 104.39876615638794},
}


@pytest.mark.parametrize("key", sorted(REFERENCE))
def test_engine_matches_independent_decimal_reference(key: str) -> None:
    """Every output key agrees with the from-scratch reference (same undefined bars)."""
    actual = _column(ENGINE_ROWS, key)
    expected = REFERENCE[key]
    assert len(actual) == len(expected)
    for index, (value, reference) in enumerate(zip(actual, expected, strict=True)):
        assert close_enough(value, reference), f"{key} bar {index}: {value} != {reference}"


@pytest.mark.parametrize("key", sorted(TALIB_GOLDEN))
def test_engine_matches_talib_golden_values(key: str) -> None:
    """Recorded TA-Lib values match the engine to float precision."""
    for index, golden in TALIB_GOLDEN[key].items():
        value = ENGINE_ROWS[index][key]
        assert value is not None
        tolerance = 1e-9 * max(1.0, abs(golden))
        assert abs(float(value) - golden) <= tolerance, f"{key} bar {index}: {value} vs {golden}"


def test_reference_tolerance_is_far_stricter_than_float() -> None:
    """The Decimal reference comparison is 1e-40 relative, not a float tolerance."""
    assert Decimal("1e-40") == TOLERANCE


def _first_fully_defined(rows: Sequence[dict[str, Decimal | None]], keys: Sequence[str]) -> int:
    """Return the first row index where every key is defined, or -1."""
    for index, row in enumerate(rows):
        if all(row[key] is not None for key in keys):
            return index
    return -1


@pytest.mark.parametrize("spec", INDICATOR_KIND_SPECS, ids=lambda spec: spec.kind.value)
def test_first_defined_bar_equals_warmup_minus_one(spec: IndicatorKindSpec) -> None:
    """Default declarations become fully defined exactly at the documented warmup."""
    for offset in (None, 3):
        if offset is not None and spec.kind is IndicatorKind.CONSTANT:
            continue
        definition = default_indicator_definition(spec, "probe", offset=offset)
        rows = calculate_indicator_rows((definition,), CANDLES)
        first = _first_fully_defined(rows, indicator_value_keys(definition))
        assert first == indicator_min_warmup(definition) - 1, (spec.kind.value, offset)


@pytest.mark.parametrize("spec", INDICATOR_KIND_SPECS, ids=lambda spec: spec.kind.value)
def test_prefix_never_changes_earlier_values(spec: IndicatorKindSpec) -> None:
    """No lookahead: truncating the future leaves every earlier value unchanged."""
    definition = default_indicator_definition(spec, "probe")
    full = calculate_indicator_rows((definition,), CANDLES)
    warmup = indicator_min_warmup(definition)
    for length in sorted({1, 2, warmup, warmup + 1, 120, len(CANDLES) - 1}):
        prefix = calculate_indicator_rows((definition,), CANDLES[:length])
        assert prefix == full[:length], (spec.kind.value, length)


@pytest.mark.parametrize("spec", INDICATOR_KIND_SPECS, ids=lambda spec: spec.kind.value)
def test_offset_is_an_exact_shift_of_completed_bars(spec: IndicatorKindSpec) -> None:
    """Offset k reports bar t - k on bar t and nothing for the first k bars."""
    if spec.kind is IndicatorKind.CONSTANT:
        return
    base = calculate_indicator_rows((default_indicator_definition(spec, "probe"),), CANDLES)
    for offset in (1, 7):
        lagged = calculate_indicator_rows(
            (default_indicator_definition(spec, "probe", offset=offset),), CANDLES
        )
        assert all(value is None for row in lagged[:offset] for value in row.values())
        assert lagged[offset:] == base[: len(base) - offset]


@pytest.mark.parametrize("spec", INDICATOR_KIND_SPECS, ids=lambda spec: spec.kind.value)
def test_empty_input_returns_zero_rows(spec: IndicatorKindSpec) -> None:
    """Zero candles yield zero rows for every kind, lagged or not."""
    offset = None if spec.kind is IndicatorKind.CONSTANT else 2
    for candidate in (None, offset):
        definition = default_indicator_definition(spec, "probe", offset=candidate)
        assert calculate_indicator_rows((definition,), ()) == ()


def test_offset_longer_than_the_series_is_all_undefined() -> None:
    """A lag beyond the supplied history never wraps or reads ahead."""
    spec = next(item for item in INDICATOR_KIND_SPECS if item.kind is IndicatorKind.IDENTITY)
    rows = calculate_indicator_rows(
        (default_indicator_definition(spec, "probe", offset=50),), CANDLES[:10]
    )
    assert [row["probe"] for row in rows] == [None] * 10


FLAT = flat_candles(80)
FLAT_PRICE = Decimal(50)


def _flat(name: str, key: str | None = None) -> list[Decimal | None]:
    """Return one output on flat candles after the declaration's warmup."""
    definition = _definitions((name,))[0]
    rows = calculate_indicator_rows((definition,), FLAT)
    warmup = indicator_min_warmup(definition)
    return [row[key or name] for row in rows[warmup - 1 :]]


@pytest.mark.parametrize(
    ("name", "key", "expected"),
    [
        ("dema", None, FLAT_PRICE),
        ("tema", None, FLAT_PRICE),
        ("hma", None, FLAT_PRICE),
        ("kama", None, FLAT_PRICE),
        ("vwma", None, FLAT_PRICE),
        ("st", "st.value", FLAT_PRICE),
        ("st", "st.direction", Decimal(-1)),
        ("sar", None, FLAT_PRICE),
        ("aroon", "aroon.up", Decimal(100)),
        ("aroon", "aroon.down", Decimal(100)),
        ("aroon", "aroon.oscillator", Decimal(0)),
        ("ichi", "ichi.senkou_a", FLAT_PRICE),
        ("ichi", "ichi.senkou_b", FLAT_PRICE),
        ("lr", "lr.value", FLAT_PRICE),
        ("lr", "lr.slope", Decimal(0)),
        ("trix", None, Decimal(0)),
        ("ppo", "ppo.ppo", Decimal(0)),
        ("ppo", "ppo.histogram", Decimal(0)),
        ("ao", None, Decimal(0)),
        ("kc", "kc.upper", FLAT_PRICE),
        ("kc", "kc.lower", FLAT_PRICE),
        ("dc", "dc.middle", FLAT_PRICE),
        ("bw", None, Decimal(0)),
        ("natr", None, Decimal(0)),
        ("hv", None, Decimal(0)),
        ("obv", "obv.obv", Decimal(0)),
        ("obv", "obv.signal", Decimal(0)),
        ("ad", "ad.ad", Decimal(0)),
        ("cmf", None, Decimal(0)),
        ("vwap", None, FLAT_PRICE),
        ("fi", None, Decimal(0)),
        ("pr", None, Decimal(100)),
    ],
)
def test_flat_prices_follow_the_documented_defined_convention(
    name: str, key: str | None, expected: Decimal
) -> None:
    """Kinds without a zero divisor stay defined on flat prices with the documented value."""
    assert set(_flat(name, key)) == {expected}


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("vortex", "vortex.plus"),
        ("vortex", "vortex.minus"),
        ("srsi", "srsi.k"),
        ("srsi", "srsi.d"),
        ("uo", None),
        ("cmo", None),
        ("tsi", "tsi.tsi"),
        ("tsi", "tsi.signal"),
        ("pb", None),
        ("chop", None),
        ("z", None),
    ],
)
def test_zero_divisors_on_flat_prices_fail_closed(name: str, key: str | None) -> None:
    """A zero range, zero true range, or zero stdev yields undefined, never 0 or 100."""
    assert set(_flat(name, key)) == {None}


def test_stochastic_rsi_is_undefined_not_noise_once_prices_go_flat() -> None:
    """A flat stretch keeps RSI constant up to rounding; the stochastic of it is undefined."""
    moving = CANDLES[:40]
    last = moving[-1]
    flat = tuple(
        Candle(
            last.starts_at + timedelta(hours=offset),
            last.close,
            last.close,
            last.close,
            last.close,
            Decimal(1),
        )
        for offset in range(1, 41)
    )
    definition = IndicatorDefinition.model_validate(
        {
            "id": "x",
            "kind": "stochastic_rsi",
            "input": "close",
            "parameters": {"rsi_period": 14, "stoch_period": 14, "k_period": 1, "d_period": 1},
        }
    )
    rows = calculate_indicator_rows((definition,), moving + flat)
    raw = [row["x.k"] for row in rows]
    assert any(value is not None for value in raw[:40])
    # Windows of 14 RSI values that start at the last moving bar or later are flat.
    assert raw[39 + 13 :] == [None] * (len(raw) - 52)


def test_zero_volume_windows_fail_closed_for_volume_weighted_kinds() -> None:
    """VWMA, CMF, and rolling VWAP divide by window volume; zero volume is undefined."""
    silent = flat_candles(30, volume="0")
    definitions = _definitions(("vwma", "cmf", "vwap", "fi"))
    rows = calculate_indicator_rows(definitions, silent)
    assert {row["vwma"] for row in rows} == {None}
    assert {row["cmf"] for row in rows} == {None}
    assert {row["vwap"] for row in rows} == {None}
    assert {row["fi"] for row in rows[13:]} == {Decimal(0)}


def _bars(*rows: tuple[str, str, str, str, str]) -> tuple[Candle, ...]:
    """Build hourly candles from (open, high, low, close, volume) text tuples."""
    return tuple(
        Candle(
            _START + timedelta(hours=index),
            Decimal(open_),
            Decimal(high),
            Decimal(low),
            Decimal(close),
            Decimal(volume),
        )
        for index, (open_, high, low, close, volume) in enumerate(rows)
    )


HAND = _bars(
    ("10", "12", "9", "11", "100"),
    ("11", "13", "10", "12", "200"),
    ("12", "12", "8", "9", "300"),
    ("9", "14", "9", "13", "400"),
    ("13", "13", "11", "11", "100"),
)


def _hand(declaration: dict[str, object]) -> tuple[dict[str, Decimal | None], ...]:
    """Compute one declaration over the five hand-checked bars."""
    return calculate_indicator_rows(
        (IndicatorDefinition.model_validate({"id": "x", **declaration}),), HAND
    )


def test_hand_verified_donchian_aroon_and_ichimoku_midpoints() -> None:
    """Window extremes on five bars, worked by hand."""
    donchian = _hand({"kind": "donchian", "input": ["high", "low"], "parameters": {"period": 3}})
    assert [row["x.upper"] for row in donchian] == [None, None, 13, 14, 14]
    assert [row["x.lower"] for row in donchian] == [None, None, 8, 8, 8]
    assert [row["x.middle"] for row in donchian] == [None, None, Decimal("10.5"), 11, 11]
    aroon = _hand({"kind": "aroon", "input": ["high", "low"], "parameters": {"period": 2}})
    # Bar 3 window = bars 1..3: highest high 14 (bar 3, 0 ago), lowest low 8 (bar 2, 1 ago).
    assert aroon[3]["x.up"] == 100
    assert aroon[3]["x.down"] == 50
    assert aroon[3]["x.oscillator"] == 50
    # Bar 4 window = bars 2..4: high 14 is 1 bar ago; low 8 is 2 bars ago.
    assert (aroon[4]["x.up"], aroon[4]["x.down"]) == (50, 0)


def test_hand_verified_obv_ad_cmf_and_vwap() -> None:
    """Cumulative and volume-weighted kinds on five bars, worked by hand."""
    obv = _hand({"kind": "obv", "input": ["close", "volume"], "parameters": {"signal_period": 2}})
    assert [row["x.obv"] for row in obv] == [0, 200, -100, 300, 200]
    assert [row["x.signal"] for row in obv] == [None, 100, 50, 100, 250]
    ad = _hand(
        {
            "kind": "accumulation_distribution",
            "input": ["high", "low", "close", "volume"],
            "parameters": {"signal_period": 2},
        }
    )
    # Multipliers: (2-1)/3, (2-1)/3, (1-3)/4, (4-1)/5, (0-2)/2.
    expected = in_engine_context(lambda: [Decimal(100) / 3, Decimal(100) / 3 + Decimal(200) / 3])
    assert ad[0]["x.ad"] is not None
    assert close_enough(ad[0]["x.ad"], expected[0])
    assert close_enough(ad[1]["x.ad"], expected[1])
    assert close_enough(ad[4]["x.ad"], Decimal(100) - 150 + 240 - 100)
    vwap = _hand(
        {
            "kind": "vwap",
            "input": ["high", "low", "close", "volume"],
            "parameters": {"period": 2},
        }
    )
    # Typical prices: 32/3 and 35/3 weighted 100 and 200 -> (3200 + 7000) / 3 / 300.
    assert close_enough(vwap[1]["x"], in_engine_context(lambda: Decimal(10200) / Decimal(900)))


def test_hand_verified_percent_rank_cmo_and_linear_regression() -> None:
    """Statistical and momentum kinds on short exact series."""
    rank = _hand({"kind": "percent_rank", "input": "close", "parameters": {"period": 2}})
    # Bar 3 close 13 vs previous 12 and 9: both <= 13 -> 100; bar 4 close 11 vs 9, 13 -> 50.
    assert [row["x"] for row in rank] == [None, None, 0, 100, 50]
    cmo = _hand({"kind": "cmo", "input": "close", "parameters": {"period": 2}})
    # Bar 2 changes +1, -3 -> 100 * (1 - 3) / 4 = -50; bar 3 changes -3, +4 -> 100 / 7.
    assert cmo[2]["x"] == -50
    assert close_enough(cmo[3]["x"], in_engine_context(lambda: Decimal(100) / Decimal(7)))
    line = calculate_indicator_rows(
        (
            IndicatorDefinition.model_validate(
                {
                    "id": "x",
                    "kind": "linear_regression",
                    "input": "close",
                    "parameters": {"period": 4},
                }
            ),
        ),
        _bars(*[(str(n), str(n), str(n), str(n), "1") for n in (3, 5, 7, 9, 11)]),
    )
    assert [row["x.slope"] for row in line] == [None, None, None, 2, 2]
    assert [row["x.value"] for row in line] == [None, None, None, 9, 11]


def test_hand_verified_choppiness_uses_exact_decimal_log10() -> None:
    """With period 10 the denominator log10(10) is exactly 1."""
    bars = _bars(*[("10", "11", "9", "10", "1") for _ in range(10)])
    rows = calculate_indicator_rows(
        (
            IndicatorDefinition.model_validate(
                {
                    "id": "x",
                    "kind": "choppiness",
                    "input": ["high", "low", "close"],
                    "parameters": {"period": 10},
                }
            ),
        ),
        bars,
    )
    # Ten true ranges of 2 over a range of 2: 100 * log10(10) / log10(10) = 100.
    assert rows[-1]["x"] == 100


def test_hand_verified_supertrend_seed_and_flip() -> None:
    """Seed is a down trend on the first ATR bar; a close above the upper band flips up."""
    bars = _bars(
        ("10", "11", "9", "10", "1"),
        ("10", "11", "9", "10", "1"),
        ("10", "20", "10", "19", "1"),
    )
    rows = calculate_indicator_rows(
        (
            IndicatorDefinition.model_validate(
                {
                    "id": "x",
                    "kind": "supertrend",
                    "input": ["high", "low", "close"],
                    "parameters": {"period": 2, "multiplier": "1"},
                }
            ),
        ),
        bars,
    )
    # ATR(2) on bar 1 = mean(2, 2) = 2: bands 10 +/- 2, seed down -> value = upper 12.
    assert (rows[1]["x.value"], rows[1]["x.direction"]) == (12, -1)
    # Bar 2: TR = max(10, 10, 0) = 10 -> ATR (2 + 10) / 2 = 6; mid 15; basic 21 / 9.
    # Final upper stays 12 (21 is not lower, prior close 10 not above 12); close 19 > 12.
    assert (rows[2]["x.value"], rows[2]["x.direction"]) == (9, 1)


def test_hand_verified_parabolic_sar_reverses_on_touch() -> None:
    """TA-Lib SAR: long start, first SAR is bar 0's low, a touch reverses to bar extremes."""
    bars = _bars(
        ("10", "11", "9", "10", "1"),
        ("10", "12", "10", "11", "1"),
        ("11", "13", "11", "12", "1"),
        ("12", "12", "8", "8", "1"),
    )
    rows = calculate_indicator_rows(
        (
            IndicatorDefinition.model_validate(
                {
                    "id": "x",
                    "kind": "parabolic_sar",
                    "input": ["high", "low"],
                    "parameters": {"step": "0.1", "max_step": "0.2"},
                }
            ),
        ),
        bars,
    )
    sar = [row["x"] for row in rows]
    assert sar[0] is None
    # Bar 1 reports the seed (bar 0's low, 9). Next SAR = 9 + 0.1 * (12 - 9) = 9.3; it
    # stays below bar 1's low 10, so 9.3.
    assert sar[1] == 9
    # Bar 2 reports 9.3. Its new high 13 raises af to 0.2: next = 9.3 + 0.2 * 3.7 = 10.04,
    # capped at the prior bar's low 10.
    assert sar[2] == Decimal("9.3")
    # Bar 3's low 8 touches 10: reverse short and report the extreme point 13.
    assert sar[3] == 13


def _spec(
    strategy: StrategyDefinition,
    *,
    evaluation_bars: int,
    htf_dataset_fingerprint: str | None = None,
) -> ResearchRunSpecification:
    """Return a 1h research spec whose warmup matches the strategy exactly.

    Every declared reference instrument (ADR 0096) is bound to a stand-in dataset.
    """
    warmup = strategy.data_requirements.warmup_bars
    starts_at = _START + timedelta(hours=warmup)
    return ResearchRunSpecification(
        schema_version="1.0",
        run_id=UUID("019faf76-6600-7000-8000-000000000065"),
        created_at=datetime(2026, 7, 29, 20, tzinfo=UTC),
        strategy_fingerprint=strategy_fingerprint(strategy),
        dataset_fingerprint="sha256:" + "4" * 64,
        htf_dataset_fingerprint=htf_dataset_fingerprint,
        reference_dataset_fingerprints=tuple(
            ReferenceInstrumentDataset(
                reference_id=reference.id,
                product_id=reference.product_id,
                timeframe=reference.timeframe,
                dataset_fingerprint="sha256:" + "6" * 64,
            )
            for reference in reference_instruments(strategy)
        ),
        evaluation=EvaluationWindow(
            starts_at=starts_at, ends_at=starts_at + timedelta(hours=evaluation_bars)
        ),
        warmup=WarmupWindow(bars=warmup, starts_at=_START),
        capital=CapitalAssumptions(quote_currency="USDC", initial_quote_balance="10000"),
        costs=CostAssumptions(
            maker_fee_rate="0.004", taker_fee_rate="0.006", fixed_slippage_bps="1"
        ),
        random_seed=7,
    )


@pytest.mark.parametrize("template", list(StrategyTemplateId), ids=lambda item: item.value)
def test_templates_evaluate_identically_in_research_and_paper_live(
    template: StrategyTemplateId,
) -> None:
    """The research trace and the paper/live latest-bar evaluator agree on sampled bars.

    Reference-instrument templates read the same synthetic daily series in both paths;
    paper/live see only daily bars closed by each decision close.
    """
    strategy = create_template_strategy(
        template=template.value, product_id="BTC-USDC", timeframe="1h"
    )
    warmup = strategy.data_requirements.warmup_bars
    evaluation_bars = len(CANDLES) - warmup
    references = {reference.id: _DAILY for reference in reference_instruments(strategy)}
    trace = evaluate_signal_trace(
        _spec(strategy, evaluation_bars=evaluation_bars),
        strategy,
        CANDLES,
        reference_candles=references,
    )
    assert len(trace.records) == evaluation_bars
    for offset in range(0, evaluation_bars, 6):
        through = CANDLES[: warmup + offset + 1]
        expected = trace.records[offset].entry_condition
        assert evaluate_latest_entry(strategy, through, reference_candles=references) is (
            expected
        ), offset


def test_catalog_templates_produce_signals_on_the_synthetic_series() -> None:
    """The new templates are live strategies, not inert examples."""
    matched: dict[str, int] = {}
    for template in (
        StrategyTemplateId.DONCHIAN_BREAKOUT,
        StrategyTemplateId.SUPERTREND_TREND,
        StrategyTemplateId.ZSCORE_MEAN_REVERSION,
    ):
        strategy = create_template_strategy(template=template.value, product_id="BTC-USDC")
        warmup = strategy.data_requirements.warmup_bars
        trace = evaluate_signal_trace(
            _spec(strategy, evaluation_bars=len(CANDLES) - warmup), strategy, CANDLES
        )
        matched[template.value] = sum(
            1 for record in trace.records if record.entry_condition == "matched"
        )
    assert all(count > 0 for count in matched.values()), matched


def test_new_kinds_work_in_htf_filters_and_on_extra_indicator_timeframes() -> None:
    """A 4h Supertrend filter and a 4h lagged Donchian evaluate on last-completed bars."""
    strategy = create_template_strategy(template="ema-trend", product_id="BTC-USDC")
    payload = strategy.model_dump(mode="json", by_alias=True)
    indicators = cast("list[dict[str, object]]", payload["indicators"])
    indicators.append(
        {
            "id": "prior_channel",
            "kind": "donchian",
            "input": ["high", "low"],
            "parameters": {"period": 5},
            "timeframe": "4h",
            "offset": 1,
        }
    )
    payload["htf_filter"] = {
        "timeframe": "4h",
        "data_requirements": {
            "warmup_bars": 6,
            "required_fields": ["open", "high", "low", "close", "volume"],
        },
        "indicators": [
            {
                "id": "htf_trend",
                "kind": "supertrend",
                "input": ["high", "low", "close"],
                "parameters": {"period": 4, "multiplier": "2"},
            }
        ],
        "when": {
            "all": [
                {
                    "left": {"indicator": "htf_trend", "series": "direction"},
                    "operator": "greater_than",
                    "right": {"literal": "0"},
                }
            ]
        },
    }
    validated = StrategyDefinition.model_validate(payload)
    assert validated.htf_filter is not None
    prior = next(item for item in validated.indicators if item.id == "prior_channel")
    assert indicator_min_warmup(prior) == 6
    four_hour = _aggregate(CANDLES, 4)
    warmup = validated.data_requirements.warmup_bars
    evaluation_bars = 120
    trace = evaluate_signal_trace(
        _spec(
            validated,
            evaluation_bars=evaluation_bars,
            htf_dataset_fingerprint="sha256:" + "5" * 64,
        ),
        validated,
        CANDLES,
        htf_candles=four_hour,
    )
    keys = trace.indicator_ids
    assert "prior_channel.upper" in keys
    assert "htf_trend.direction" in keys
    outcomes = {record.entry_condition for record in trace.records}
    assert EntryConditionOutcome.UNDEFINED not in outcomes
    for offset in range(0, evaluation_bars, 7):
        through = CANDLES[: warmup + offset + 1]
        paper_live = evaluate_latest_entry(validated, through, four_hour, {"4h": four_hour})
        assert paper_live is trace.records[offset].entry_condition, offset


def _aggregate(candles: Sequence[Candle], width: int) -> tuple[Candle, ...]:
    """Aggregate complete groups of hourly candles into ``width``-hour bars."""
    groups = [candles[index : index + width] for index in range(0, len(candles), width)]
    return tuple(
        Candle(
            group[0].starts_at,
            group[0].open,
            max(c.high for c in group),
            min(c.low for c in group),
            group[-1].close,
            sum((c.volume for c in group), start=Decimal(0)),
        )
        for group in groups
        if len(group) == width
    )


def test_offset_zero_is_omitted_and_keeps_fingerprints_byte_identical() -> None:
    """offset: 0 normalizes to omitted, so documents and fingerprints are unchanged."""
    strategy = create_template_strategy()
    payload = strategy.model_dump(mode="json", by_alias=True)
    with_zero = cast("list[dict[str, object]]", payload["indicators"])
    for item in with_zero:
        item["offset"] = 0
    rebuilt = StrategyDefinition.model_validate(payload)
    assert strategy_fingerprint(rebuilt) == strategy_fingerprint(strategy)
    assert all(item.offset is None for item in rebuilt.indicators)
    assert '"offset"' not in rebuilt.model_dump_json()


@pytest.mark.parametrize(
    ("declaration", "message"),
    [
        (
            {"kind": "constant", "parameters": {"value": "1"}, "offset": 1},
            "constant must omit offset",
        ),
        (
            {"kind": "sma", "input": "close", "parameters": {"period": 2}, "offset": -1},
            "greater than or equal to 0",
        ),
        (
            {"kind": "sma", "input": "close", "parameters": {"period": 2}, "offset": 501},
            "less than or equal to 500",
        ),
        (
            {
                "kind": "kama",
                "input": "close",
                "parameters": {"period": 10, "fast_period": 30, "slow_period": 30},
            },
            "kama fast_period must be less than slow_period",
        ),
        (
            {
                "kind": "ichimoku",
                "input": ["high", "low"],
                "parameters": {"tenkan_period": 26, "kijun_period": 9, "senkou_b_period": 52},
            },
            "tenkan_period < kijun_period < senkou_b_period",
        ),
        (
            {
                "kind": "parabolic_sar",
                "input": ["high", "low"],
                "parameters": {"step": "0.3", "max_step": "0.2"},
            },
            "step must be at most max_step",
        ),
        (
            {
                "kind": "supertrend",
                "input": ["high", "low", "close"],
                "parameters": {"period": 10, "multiplier": "0"},
            },
            "supertrend multiplier must be greater than 0",
        ),
        (
            {
                "kind": "keltner",
                "input": ["high", "low", "close"],
                "parameters": {"period": 20, "atr_period": 10, "multiplier": "11"},
            },
            "keltner multiplier must be at most 10",
        ),
        (
            {
                "kind": "tsi",
                "input": "close",
                "parameters": {"long_period": 13, "short_period": 25, "signal_period": 7},
            },
            "tsi short_period must be less than long_period",
        ),
        (
            {
                "kind": "ultimate_oscillator",
                "input": ["high", "low", "close"],
                "parameters": {"short_period": 14, "medium_period": 7, "long_period": 28},
            },
            "short_period < medium_period < long_period",
        ),
        (
            {"kind": "donchian", "input": ["high", "low", "close"], "parameters": {"period": 20}},
            "donchian input must be high, low in canonical order",
        ),
        (
            {"kind": "vwma", "input": "close", "parameters": {"period": 20}},
            "vwma input must be close, volume in canonical order",
        ),
        (
            {"kind": "trix", "input": "high", "parameters": {"period": 9}},
            "trix input must be close",
        ),
        (
            {"kind": "obv", "input": ["close", "volume"], "parameters": {"period": 20}},
            "obv parameters must declare signal_period",
        ),
        (
            {"kind": "natr", "input": ["high", "low", "close"], "parameters": {"period": 101}},
            "NATR period exceeds 100",
        ),
        (
            {
                "kind": "historical_volatility",
                "input": "close",
                "parameters": {"period": 20, "annualization_periods": 0},
            },
            "greater than or equal to 1",
        ),
    ],
)
def test_schema_fails_closed_on_invalid_declarations(
    declaration: dict[str, object], message: str
) -> None:
    """Bad inputs, bounds, cross-parameter rules, and lags fail validation."""
    with pytest.raises(ValidationError, match=message.replace("(", r"\(").replace(")", r"\)")):
        IndicatorDefinition.model_validate({"id": "probe", **declaration})


def test_offset_adds_to_warmup_and_strategy_warmup_must_cover_it() -> None:
    """A lagged declaration needs its base warmup plus the lag."""
    lagged = IndicatorDefinition.model_validate(
        {
            "id": "prior_high",
            "kind": "highest",
            "input": "high",
            "parameters": {"period": 20},
            "offset": 1,
        }
    )
    assert indicator_min_warmup(lagged) == 21
    payload = create_template_strategy().model_dump(mode="json", by_alias=True)
    indicators = cast("list[dict[str, object]]", payload["indicators"])
    indicators.append(
        {
            "id": "prior_high",
            "kind": "highest",
            "input": "high",
            "parameters": {"period": 60},
            "offset": 1,
        }
    )
    with pytest.raises(ValidationError, match="warmup_bars must cover"):
        StrategyDefinition.model_validate(payload)
    cast("dict[str, object]", payload["data_requirements"])["warmup_bars"] = 61
    assert StrategyDefinition.model_validate(payload).data_requirements.warmup_bars == 61


def test_reference_helpers_are_exercised_for_every_new_kind() -> None:
    """Every kind added by the wider catalog has at least one reference comparison."""
    referenced = {str(DECLARATIONS[name.split(".")[0]]["kind"]) for name in REFERENCE}
    new_kinds = {spec.kind.value for spec in INDICATOR_KIND_SPECS[21:]}
    assert len(new_kinds) == 32
    assert new_kinds <= referenced


def test_default_declarations_validate_for_every_kind() -> None:
    """Builder defaults are schema-valid documents for all 53 kinds."""
    assert len(INDICATOR_KIND_SPECS) == 53
    for spec in INDICATOR_KIND_SPECS:
        assert default_indicator_definition(spec, "probe").kind is spec.kind
