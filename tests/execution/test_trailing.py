"""ATR trailing ratchet shared by backtest, paper, and live."""

from decimal import Decimal

from thytrader.execution.trailing import ratcheted_long_stop


def test_fill_bar_records_extreme_without_raising_stop() -> None:
    """The fill bar stores the highest high and leaves the initial stop alone."""
    state = ratcheted_long_stop(
        current_stop=Decimal("90"),
        trail_extreme=None,
        bar_high=Decimal("110"),
        atr=Decimal("2"),
        multiple=Decimal("1.5"),
        price_increment=Decimal("0.01"),
        ratchet=False,
    )
    assert state.stop_price == Decimal("90")
    assert state.trail_extreme == Decimal("110")


def test_later_bar_raises_stop_and_never_decreases() -> None:
    """A later bar ratchets the stop up from the extreme and ignores a lower high."""
    raised = ratcheted_long_stop(
        current_stop=Decimal("90"),
        trail_extreme=Decimal("110"),
        bar_high=Decimal("120"),
        atr=Decimal("2"),
        multiple=Decimal("1.5"),
        price_increment=Decimal("0.01"),
        ratchet=True,
    )
    assert raised.trail_extreme == Decimal("120")
    assert raised.stop_price == Decimal("117.00")
    lowered = ratcheted_long_stop(
        current_stop=raised.stop_price,
        trail_extreme=raised.trail_extreme,
        bar_high=Decimal("100"),
        atr=Decimal("2"),
        multiple=Decimal("1.5"),
        price_increment=Decimal("0.01"),
        ratchet=True,
    )
    assert lowered.trail_extreme == Decimal("120")
    assert lowered.stop_price == raised.stop_price


def test_missing_atr_leaves_stop_unchanged() -> None:
    """Undefined ATR must not collapse the stop to zero."""
    state = ratcheted_long_stop(
        current_stop=Decimal("90"),
        trail_extreme=Decimal("110"),
        bar_high=Decimal("130"),
        atr=None,
        multiple=Decimal("1.5"),
        price_increment=Decimal("0.01"),
        ratchet=True,
    )
    assert state.stop_price == Decimal("90")
    assert state.trail_extreme == Decimal("130")
