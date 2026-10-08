"""Backtest submission contracts: request and result models, errors, assumption checks.

The agent start request, the exact internal submission (also the durable job
payload), the result identities, the redacted and caller-input errors, and the
revalidation of untrusted capital, cost, and window assumptions. This module
imports no other submission module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from thytrader.research.models import (
    AdditionalInstrumentDataset,
    CapitalAssumptions,
    CostAssumptions,
    DecimalInputText,
    EvaluationWindow,
    IndicatorTimeframeDataset,
    ReferenceInstrumentDataset,
    reject_removed_engine_selection,
)
from thytrader.research.stress import ExecutionStress

if TYPE_CHECKING:
    from thytrader.market_data.products import SpotQuoteCurrency


class BacktestAssumptions(BaseModel):
    """Extra datasets, window, capital, and costs for one unified-model simulation.

    The primary ``dataset_fingerprint`` lives on the subclasses: required on the
    exact internal submission, optional on the agent start (ADR 0089 binds the
    newest complete catalog dataset when it is omitted). There is no engine
    selector: every run uses the single ``thytrader-backtest`` model (ADR 0083).
    ``spread_bps`` is the optional constant spread stress (omitted means 0). Decimal
    fields accept JSON numbers as well as strings; numbers become canonical decimal
    strings before any fingerprint is computed (ADR 0094). ``reference_dataset_fingerprints``
    binds each declared reference instrument (ADR 0096); omitted ones bind the newest
    complete catalog dataset like every other clock.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    htf_dataset_fingerprint: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    indicator_dataset_fingerprints: tuple[IndicatorTimeframeDataset, ...] = ()
    additional_instrument_datasets: tuple[AdditionalInstrumentDataset, ...] = ()
    reference_dataset_fingerprints: tuple[ReferenceInstrumentDataset, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    evaluation_start: datetime | None = None
    evaluation_end: datetime | None = None
    initial_quote_balance: DecimalInputText
    maker_fee_rate: DecimalInputText
    taker_fee_rate: DecimalInputText
    fixed_slippage_bps: DecimalInputText
    spread_bps: DecimalInputText | None = None
    execution_stress: ExecutionStress | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="before")
    @classmethod
    def reject_engine_selection(cls, data: object) -> object:
        """Reject the retired engine selector with an explicit migration message."""
        return reject_removed_engine_selection(data)

    @model_validator(mode="after")
    def validate_assumptions(self) -> Self:
        """Reject assumptions that cannot form one valid immutable research run."""
        _validate_submission_assumptions(self)
        return self


class BacktestSubmissionRequest(BacktestAssumptions):
    """Internal submission bound to one exact strategy snapshot fingerprint.

    HTTP and CLI callers send :class:`BacktestStartRequest` (a ``strategy_id``);
    the server snapshots the current definition, binds every dataset, and builds
    this request, which is also the durable async-job payload.
    """

    dataset_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    strategy_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class BacktestStartRequest(BacktestAssumptions):
    """Agent/browser backtest start: the server snapshots ``strategy_id``'s current rules.

    Any omitted dataset (primary, HTF, extra clock, or additional instrument) is bound
    to the newest complete catalog dataset before :meth:`submission` (ADR 0089).
    """

    dataset_fingerprint: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    strategy_id: UUID

    def submission(self, strategy_fingerprint: str) -> BacktestSubmissionRequest:
        """Bind these assumptions to the snapshot taken for ``strategy_id``.

        Every dataset must already be bound; an omitted primary dataset fails validation.
        """
        payload = self.model_dump(mode="python", exclude={"strategy_id"})
        return BacktestSubmissionRequest.model_validate(
            {**payload, "strategy_fingerprint": strategy_fingerprint}
        )


@dataclass(frozen=True, slots=True)
class BacktestSubmissionResult:
    """Immutable run and result identities returned after deterministic publication."""

    run_fingerprint: str
    result_fingerprint: str


class BacktestSubmissionError(RuntimeError):
    """Report a redacted submission failure without granting trading authority."""


class BacktestSubmissionRejectedError(ValueError):
    """Report a caller-input rejection (dataset/window mismatch) before any I/O."""


def _validate_submission_assumptions(
    request: BacktestAssumptions,
    *,
    quote_currency: SpotQuoteCurrency = "USD",
) -> None:
    """Revalidate every untrusted simulation assumption before source or persistence I/O."""
    if request.evaluation_start is None and request.evaluation_end is None:
        pass
    elif request.evaluation_start is None or request.evaluation_end is None:
        raise ValueError("evaluation_start and evaluation_end must both be omitted or both be set.")
    else:
        EvaluationWindow(starts_at=request.evaluation_start, ends_at=request.evaluation_end)
    CapitalAssumptions(
        quote_currency=quote_currency,
        initial_quote_balance=request.initial_quote_balance,
    )
    _cost_assumptions(request)


def _cost_assumptions(request: BacktestAssumptions) -> CostAssumptions:
    """Build the published fee, slippage, and optional spread-stress assumptions."""
    return CostAssumptions(
        maker_fee_rate=request.maker_fee_rate,
        taker_fee_rate=request.taker_fee_rate,
        fixed_slippage_bps=request.fixed_slippage_bps,
        spread_bps=request.spread_bps if request.spread_bps is not None else "0",
        execution_stress=request.execution_stress,
    )
