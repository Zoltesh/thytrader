"""Agent/browser study start body: strategies by id, snapshotted by the server (ADR 0082).

Callers name strategies by ``strategy_id``. The server snapshots each one's
current (valid) definition and substitutes the snapshot fingerprints, producing
the internal :class:`~thytrader.research.studies.ResearchStudyRequest` that is
planned, fingerprinted, and stored as the async job payload.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic field type.
from typing import TYPE_CHECKING, Literal
from uuid import UUID  # noqa: TC003 - Pydantic field type.

from pydantic import BaseModel, ConfigDict, Field, model_validator

from thytrader.research.models import (
    IndicatorTimeframeDataset,
    reject_removed_engine_selection,
)
from thytrader.research.parameter_sweep import ParameterAxis, SelectionMetric
from thytrader.research.studies import (
    STUDY_CONTRACT_VERSION,
    FoldMode,
    ResearchStudyRequest,
    StudyKind,
    StudyPlanningError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"


class _FrozenStartModel(BaseModel):
    """Reject unknown fields and prevent mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class StudyMarketStart(_FrozenStartModel):
    """One cross-market leg: a strategy (by id) bound to its verified datasets."""

    strategy_id: UUID
    dataset_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    htf_dataset_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    indicator_dataset_fingerprints: tuple[IndicatorTimeframeDataset, ...] = ()


class ResearchStudyStartRequest(_FrozenStartModel):
    """Study assumptions naming strategies by id; kind rules match ResearchStudyRequest."""

    schema_version: Literal["thytrader-research-study-v1"] = STUDY_CONTRACT_VERSION
    kind: StudyKind
    evaluation_start: datetime
    evaluation_end: datetime
    initial_quote_balance: str
    maker_fee_rate: str
    taker_fee_rate: str
    fixed_slippage_bps: str
    spread_bps: str | None = None
    strategy_id: UUID | None = None
    dataset_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    htf_dataset_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    indicator_dataset_fingerprints: tuple[IndicatorTimeframeDataset, ...] = ()
    oos_fraction: str | None = None
    embargo_bars: int = Field(default=0, ge=0, le=10_000)
    in_sample_bars: int | None = Field(default=None, ge=1, le=100_000)
    out_of_sample_bars: int | None = Field(default=None, ge=1, le=100_000)
    step_bars: int | None = Field(default=None, ge=1, le=100_000)
    fold_mode: FoldMode = FoldMode.ROLLING
    markets: tuple[StudyMarketStart, ...] | None = None
    candidate_strategy_ids: tuple[UUID, ...] = ()
    parameter_axes: tuple[ParameterAxis, ...] = ()
    selection_metric: SelectionMetric = SelectionMetric.TOTAL_RETURN_FRACTION

    @model_validator(mode="before")
    @classmethod
    def reject_engine_selection(cls, data: object) -> object:
        """Reject the retired engine selector with an explicit migration message."""
        return reject_removed_engine_selection(data)

    def strategy_ids(self) -> tuple[UUID, ...]:
        """Every strategy the study uses, primary first, without duplicates."""
        ordered: dict[UUID, None] = {}
        if self.strategy_id is not None:
            ordered[self.strategy_id] = None
        for market in self.markets or ():
            ordered[market.strategy_id] = None
        for candidate in self.candidate_strategy_ids:
            ordered[candidate] = None
        return tuple(ordered)

    def primary_strategy_id(self) -> UUID:
        """The strategy the study is filed under (base strategy or first market)."""
        identities = self.strategy_ids()
        if not identities:
            raise StudyPlanningError("A study requires strategy_id or markets[].strategy_id.")
        return identities[0]

    def to_request(self, fingerprints: Mapping[UUID, str]) -> ResearchStudyRequest:
        """Substitute each strategy's snapshot fingerprint and validate kind rules."""
        payload = self.model_dump(
            mode="python",
            exclude={"strategy_id", "markets", "candidate_strategy_ids"},
        )
        payload["strategy_fingerprint"] = (
            None if self.strategy_id is None else fingerprints[self.strategy_id]
        )
        payload["candidate_strategy_fingerprints"] = tuple(
            fingerprints[item] for item in self.candidate_strategy_ids
        )
        payload["markets"] = (
            None
            if self.markets is None
            else tuple(
                {
                    **market.model_dump(mode="python", exclude={"strategy_id"}),
                    "strategy_fingerprint": fingerprints[market.strategy_id],
                }
                for market in self.markets
            )
        )
        return ResearchStudyRequest.model_validate(payload)
