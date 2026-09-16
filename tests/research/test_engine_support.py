"""Tests for the V1/V2/V3 engine-support matrix."""

from __future__ import annotations

from thytrader.research.engine_support import engine_support_matrix


def test_engine_support_matrix_has_v3_and_study_rows() -> None:
    """The matrix names all three bar engines and Phase 11 study composition."""
    matrix = engine_support_matrix()
    assert matrix.engines == (
        "thytrader-bar-backtest-v1",
        "thytrader-bar-backtest-v2",
        "thytrader-bar-backtest-v3",
    )
    by_label = {row.label: row for row in matrix.rows}
    spread = by_label["Constant spread stress assumption"]
    assert spread.v1 is False and spread.v2 is True and spread.v3 is False
    maker = by_label["Maker-only / marketable entry preference"]
    assert maker.v1 is False and maker.v2 is False and maker.v3 is True
    studies = by_label["Walk-forward / OOS / cross-market studies"]
    assert studies.v1 is True and studies.v2 is True and studies.v3 is True
    assert "0044" in studies.note
    htf = by_label["HTF filter (optional closed-bar AND with LTF entry)"]
    assert htf.v1 is True and htf.v2 is True and htf.v3 is True
    assert "paper/live" in htf.note
    extra = by_label["Per-indicator timeframes"]
    assert extra.v1 is True and extra.v2 is True and extra.v3 is True
    assert "last-completed" in extra.note
    indicators = next(row for row in matrix.rows if row.label.startswith("Indicators:"))
    assert "stochastic" in indicators.label
    assert "ADX" in indicators.label
    assert "sample stdev" in indicators.label
