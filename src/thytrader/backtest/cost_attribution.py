"""Exact closed-trade fee attribution outside immutable simulation result bytes."""

from __future__ import annotations

from decimal import Context, Decimal, Inexact, InvalidOperation, Overflow, localcontext
from hashlib import sha256
import json
import re
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from thytrader.backtest.models import BacktestResult, backtest_result_fingerprint
from thytrader.research.indicators import canonical_decimal
from thytrader.research.models import FingerprintText

_PLACEHOLDER = "sha256:" + "0" * 64
_PLAIN_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")


def _exact_decimal_text(value: str) -> str:
    """Allow canonical finite sums to exceed the source ledger's 64 significant digits."""
    if _PLAIN_DECIMAL.fullmatch(value) is None:
        raise ValueError("attribution amounts must be canonical plain decimal strings")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("attribution amounts must be finite decimal strings") from error
    if not parsed.is_finite() or canonical_decimal(parsed) != value:
        raise ValueError("attribution amounts must be canonical finite decimal strings")
    return value


AttributionDecimalText = Annotated[
    str, Field(strict=True, max_length=12500), AfterValidator(_exact_decimal_text)
]


class BacktestCostAttribution(BaseModel):
    """Sum recorded closed-trade amounts, preserving execution costs and rounding evidence.

    Fill-price PnL already includes modeled spread/slippage. ``accounting_residual`` is
    net minus (before-fees PnL minus both fees); ``summary_net_pnl_delta`` is the canonical
    summary net minus ledger net. Neither difference is silently attributed to fees.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    attribution_contract_version: Literal["thytrader-cost-attribution-v1"] = (
        "thytrader-cost-attribution-v1"
    )
    attribution_fingerprint: FingerprintText = _PLACEHOLDER
    result_fingerprint: FingerprintText
    run_fingerprint: FingerprintText
    trade_count: int = Field(ge=0)
    fill_price_pnl_before_fees: AttributionDecimalText
    entry_fees: AttributionDecimalText
    exit_fees: AttributionDecimalText
    net_pnl: AttributionDecimalText
    accounting_residual: AttributionDecimalText
    summary_net_pnl_delta: AttributionDecimalText

    @model_validator(mode="after")
    def require_valid_evidence(self) -> Self:
        """Reject negative fees and bind every derived field to its own content identity."""
        if Decimal(self.entry_fees) < 0 or Decimal(self.exit_fees) < 0:
            raise ValueError("attribution fees cannot be negative")
        payload = json.dumps(
            self.model_dump(mode="json", exclude={"attribution_fingerprint"}),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        expected = f"sha256:{sha256(payload).hexdigest()}"
        if self.attribution_fingerprint == _PLACEHOLDER:
            object.__setattr__(self, "attribution_fingerprint", expected)
        elif self.attribution_fingerprint != expected:
            raise ValueError("attribution fingerprint does not match its evidence")
        return self


def compute_cost_attribution(result: BacktestResult) -> BacktestCostAttribution:
    """Aggregate recorded amounts exactly, independent of the caller's Decimal context."""
    gross = tuple(Decimal(trade.gross_pnl) for trade in result.trades)
    entry = tuple(Decimal(trade.entry.fee) for trade in result.trades)
    exit_fees = tuple(Decimal(trade.exit.fee) for trade in result.trades)
    net = tuple(Decimal(trade.net_pnl) for trade in result.trades)
    summary_net = Decimal(result.summary.total_net_pnl)
    values = gross + entry + exit_fees + net + (summary_net,)
    precision = (
        max(value.adjusted() for value in values)
        - min(value.adjusted() - len(value.as_tuple().digits) + 1 for value in values)
        + len(str(len(values)))
        + 3
    )
    with localcontext(
        Context(
            prec=precision, Emin=-20000, Emax=20000, traps=[Inexact, InvalidOperation, Overflow]
        )
    ):
        gross_total = sum(gross, start=Decimal(0))
        entry_total = sum(entry, start=Decimal(0))
        exit_total = sum(exit_fees, start=Decimal(0))
        net_total = sum(net, start=Decimal(0))
        return BacktestCostAttribution(
            result_fingerprint=backtest_result_fingerprint(result),
            run_fingerprint=result.run_fingerprint,
            trade_count=len(result.trades),
            fill_price_pnl_before_fees=canonical_decimal(gross_total),
            entry_fees=canonical_decimal(entry_total),
            exit_fees=canonical_decimal(exit_total),
            net_pnl=canonical_decimal(net_total),
            accounting_residual=canonical_decimal(
                net_total - gross_total + entry_total + exit_total
            ),
            summary_net_pnl_delta=canonical_decimal(summary_net - net_total),
        )
