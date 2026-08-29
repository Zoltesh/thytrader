"""Safe browser-authoring defaults for the implemented conservative strategy profile."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import secrets
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from thytrader.strategies.models import (
    AllCondition,
    AtrMultipleStop,
    ComparisonCondition,
    ComparisonOperator,
    DataRequirements,
    DisabledTrailingStop,
    EntryDefinition,
    ExecutionPreferences,
    ExitDefinition,
    IndicatorDefinition,
    IndicatorKind,
    IndicatorOperand,
    IndicatorParameters,
    Instrument,
    LiteralOperand,
    PortfolioLimits,
    RewardRiskTakeProfit,
    RiskFractionSizing,
    StrategyDefinition,
    StrategyMetadata,
    StrategyStatus,
    TimeExit,
)


@dataclass(frozen=True, slots=True)
class StrategyDraft:
    """One validated editable definition plus its optimistic-concurrency revision."""

    definition: StrategyDefinition
    revision: int


@runtime_checkable
class StrategyDraftStore(Protocol):
    """Persist editable drafts without granting publication or trading authority."""

    async def create_draft(self, definition: StrategyDefinition) -> StrategyDraft:
        """Persist and return one validated draft definition."""
        ...

    async def list_drafts(self) -> tuple[StrategyDraft, ...]:
        """Return every saved editable draft in stable creation order."""
        ...

    async def save_draft(
        self, definition: StrategyDefinition, *, expected_revision: int
    ) -> StrategyDraft:
        """Replace a draft only when its durable revision still matches."""
        ...

    async def delete_draft(self, strategy_id: UUID, version: int) -> None:
        """Consume a draft after it has been published as immutable evidence."""
        ...


class DisabledStrategyDraftStore:
    """Fail closed when durable strategy-draft storage is unavailable."""

    async def create_draft(self, definition: StrategyDefinition) -> StrategyDraft:
        """Refuse an unsafely ephemeral strategy draft."""
        del definition
        raise RuntimeError("Strategy draft storage is unavailable.")

    async def list_drafts(self) -> tuple[StrategyDraft, ...]:
        """Refuse draft discovery without durable storage."""
        raise RuntimeError("Strategy draft storage is unavailable.")

    async def save_draft(
        self, definition: StrategyDefinition, *, expected_revision: int
    ) -> StrategyDraft:
        """Refuse mutation without durable strategy-draft storage."""
        del definition, expected_revision
        raise RuntimeError("Strategy draft storage is unavailable.")

    async def delete_draft(self, strategy_id: UUID, version: int) -> None:
        """Refuse mutable-state removal without durable storage."""
        del strategy_id, version
        raise RuntimeError("Strategy draft storage is unavailable.")


def create_cloned_draft(
    source: StrategyDefinition, *, now: datetime | None = None
) -> StrategyDefinition:
    """Derive a fresh draft identity from immutable evidence without changing semantics."""
    created_at = (now or datetime.now(UTC)).astimezone(UTC)
    created_at = created_at.replace(microsecond=(created_at.microsecond // 1_000) * 1_000)
    payload: dict[str, Any] = source.model_dump(mode="python")
    payload.update(
        {
            "strategy_id": _uuid7(created_at),
            "version": 1,
            "name": f"{source.name} (clone)",
            "status": StrategyStatus.DRAFT,
            "created_at": created_at,
        }
    )
    return StrategyDefinition.model_validate(payload)


def create_reference_draft(*, now: datetime | None = None) -> StrategyDefinition:
    """Construct one server-identified reference draft for the durable authoring boundary."""
    created_at = (now or datetime.now(UTC)).astimezone(UTC)
    created_at = created_at.replace(microsecond=(created_at.microsecond // 1_000) * 1_000)
    return StrategyDefinition(
        schema_version="1.0",
        strategy_id=_uuid7(created_at),
        version=1,
        name="BTC hourly EMA trend",
        description="Reference research strategy; not trading authority.",
        status=StrategyStatus.DRAFT,
        created_at=created_at,
        instrument=Instrument(product_id="BTC-USD", base_currency="BTC", quote_currency="USD"),
        timeframe="1h",
        data_requirements=DataRequirements(
            warmup_bars=50,
            required_fields=("open", "high", "low", "close", "volume"),
        ),
        indicators=(
            IndicatorDefinition(
                id="fast",
                kind=IndicatorKind.EMA,
                input="close",
                parameters=IndicatorParameters(period=20),
            ),
            IndicatorDefinition(
                id="slow",
                kind=IndicatorKind.EMA,
                input="close",
                parameters=IndicatorParameters(period=50),
            ),
            IndicatorDefinition(
                id="rsi",
                kind=IndicatorKind.RSI,
                input="close",
                parameters=IndicatorParameters(period=14),
            ),
            IndicatorDefinition(
                id="atr",
                kind=IndicatorKind.ATR,
                input=("high", "low", "close"),
                parameters=IndicatorParameters(period=14),
            ),
        ),
        entry=EntryDefinition(
            side="long",
            when=AllCondition(
                all=(
                    ComparisonCondition(
                        left=IndicatorOperand(indicator="fast"),
                        operator=ComparisonOperator.CROSSES_ABOVE,
                        right=IndicatorOperand(indicator="slow"),
                    ),
                    ComparisonCondition(
                        left=IndicatorOperand(indicator="rsi"),
                        operator=ComparisonOperator.GTE,
                        right=LiteralOperand(literal="50"),
                    ),
                )
            ),
            cooldown_bars=3,
            max_open_positions=1,
        ),
        sizing=RiskFractionSizing(
            kind="risk_fraction",
            risk_fraction="0.005",
            min_quote_notional="10",
            max_quote_notional="100",
        ),
        portfolio_limits=PortfolioLimits(
            max_strategy_exposure_fraction="0.10", max_concurrent_positions=1
        ),
        exits=ExitDefinition(
            initial_stop=AtrMultipleStop(kind="atr_multiple", atr_indicator="atr", multiple="2"),
            take_profit=RewardRiskTakeProfit(kind="reward_risk", multiple="2"),
            trailing_stop=DisabledTrailingStop(enabled=False),
            time_exit=TimeExit(max_bars_held=96),
        ),
        execution=ExecutionPreferences(
            entry_preference="maker_only", max_entry_wait_bars=2, on_unfilled_entry="cancel"
        ),
        metadata=StrategyMetadata(tags=("reference",), notes=()),
    )


def _uuid7(created_at: datetime) -> UUID:
    """Create a UUIDv7 whose timestamp equals the supplied UTC millisecond."""
    milliseconds = int(created_at.timestamp() * 1_000)
    value = (
        (milliseconds << 80)
        | (0x7 << 76)
        | (secrets.randbits(12) << 64)
        | (0b10 << 62)
        | secrets.randbits(62)
    )
    return UUID(int=value)
