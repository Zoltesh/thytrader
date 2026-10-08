"""Stops, take-profits, trailing stops, time exits, and signal exits."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from thytrader.strategies.schema.conditions import ConditionGroup, _require_bounded_condition_tree
from thytrader.strategies.schema.primitives import DecimalText, _FrozenModel


class AtrMultipleStop(_FrozenModel):
    """Define an initial stop as a positive multiple of a named ATR."""

    kind: Literal["atr_multiple"]
    atr_indicator: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    multiple: DecimalText

    @field_validator("multiple")
    @classmethod
    def require_bounded_multiple(cls, value: str) -> str:
        """Require the documented ATR stop-distance range."""
        if not Decimal("0.5") <= Decimal(value) <= Decimal("10"):
            raise ValueError("ATR multiple must be between 0.5 and 10")
        return value


class RewardRiskTakeProfit(_FrozenModel):
    """Define take profit as a positive reward-to-risk ratio."""

    kind: Literal["reward_risk"]
    multiple: DecimalText

    @field_validator("multiple")
    @classmethod
    def require_bounded_multiple(cls, value: str) -> str:
        """Require the documented reward-to-risk target range."""
        if not Decimal("0.5") <= Decimal(value) <= Decimal("10"):
            raise ValueError("reward-risk multiple must be between 0.5 and 10")
        return value


class NoTakeProfit(_FrozenModel):
    """Declare no take-profit: exits are the stop, the optional ATR trail, and the time exit.

    Canonical JSON is only ``{"kind": "none"}``. Paper and live rest no take-profit order;
    live protects the book with a venue stop-limit instead of a TP/SL bracket (ADR 0090).
    """

    kind: Literal["none"]


TakeProfitDefinition = Annotated[
    RewardRiskTakeProfit | NoTakeProfit,
    Field(discriminator="kind"),
]


class DisabledTrailingStop(_FrozenModel):
    """Explicitly disable trailing stops. Canonical JSON is only ``enabled: false``."""

    enabled: Literal[False]


class AtrTrailingStop(_FrozenModel):
    """Raise a long stop from the highest high using a named ATR multiple."""

    enabled: Literal[True]
    kind: Literal["atr_multiple"]
    atr_indicator: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    multiple: DecimalText

    @field_validator("multiple")
    @classmethod
    def require_bounded_multiple(cls, value: str) -> str:
        """Require the same ATR-multiple range as the initial stop."""
        if not Decimal("0.5") <= Decimal(value) <= Decimal("10"):
            raise ValueError("ATR trailing multiple must be between 0.5 and 10")
        return value


TrailingStopDefinition = Annotated[
    DisabledTrailingStop | AtrTrailingStop,
    Field(discriminator="enabled"),
]


class TimeExit(_FrozenModel):
    """Close intent after a bounded number of completed holding bars."""

    max_bars_held: int = Field(ge=1, le=100_000)


class SignalExit(_FrozenModel):
    """Close an open position when a closed-bar condition tree matches (ADR 0093).

    ``when`` uses the ``entry.when`` grammar and may reference the same decision-list
    indicators (never HTF-filter indicators). It is evaluated on every closed bar after
    the fill bar while a position is open, and a match exits as a taker at that bar's
    close, like the time exit. The initial stop stays mandatory: the protective stop, the
    optional trail, the take-profit, and the time exit still apply, and the stop wins a
    same-bar tie.
    """

    when: ConditionGroup

    @model_validator(mode="after")
    def validate_condition_complexity(self) -> Self:
        """Reject condition trees whose bounded grammar could exhaust consumers."""
        _require_bounded_condition_tree(self.when)
        return self


class ExitDefinition(_FrozenModel):
    """Declare initial-stop, optional take-profit, optional ATR trailing, and time-exit policy.

    ``signal_exit`` is optional and omitted from canonical JSON when absent, so every
    document written before ADR 0093 keeps its bytes and fingerprint.
    """

    initial_stop: AtrMultipleStop
    take_profit: TakeProfitDefinition
    trailing_stop: TrailingStopDefinition
    time_exit: TimeExit
    signal_exit: SignalExit | None = Field(default=None, exclude_if=lambda value: value is None)


def signal_exit_condition(exits: ExitDefinition) -> ConditionGroup | None:
    """Return the ``exits.signal_exit.when`` tree, or None when no signal exit is declared."""
    signal_exit = exits.signal_exit
    return None if signal_exit is None else signal_exit.when


def atr_trailing_stop(exits: ExitDefinition) -> AtrTrailingStop | None:
    """Return the enabled ATR trailing policy, or None when trailing is disabled."""
    stop = exits.trailing_stop
    if isinstance(stop, AtrTrailingStop):
        return stop
    return None


def reward_risk_multiple(exits: ExitDefinition) -> Decimal | None:
    """Return the take-profit reward-to-risk multiple, or None when the strategy has no TP."""
    take_profit = exits.take_profit
    if isinstance(take_profit, RewardRiskTakeProfit):
        return Decimal(take_profit.multiple)
    return None
