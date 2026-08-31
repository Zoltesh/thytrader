"""Typed application boundary for immutable backtest submission."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import secrets
from typing import TYPE_CHECKING, Literal, Protocol, Self, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from thytrader.backtest.models import backtest_result_fingerprint
from thytrader.backtest.service import evaluate_and_publish_backtest
from thytrader.persistence.postgres_backtests import PostgresBacktestResultStore
from thytrader.persistence.postgres_research_runs import PostgresResearchRunStore
from thytrader.persistence.postgres_strategies import PostgresStrategyPublicationStore
from thytrader.research.models import (
    BarExecutionAssumptions,
    BrokerAssumptions,
    CapitalAssumptions,
    CostAssumptions,
    EvaluationWindow,
    ResearchRunSpecification,
    WarmupWindow,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.market_data.datasets import DatasetStore


class BacktestSubmissionRequest(BaseModel):
    """Browser-supplied immutable simulation assumptions with no execution authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    strategy_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    dataset_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    evaluation_start: datetime
    evaluation_end: datetime
    initial_quote_balance: str
    maker_fee_rate: str
    taker_fee_rate: str
    fixed_slippage_bps: str
    engine_contract_version: Literal["thytrader-bar-backtest-v1", "thytrader-bar-backtest-v2"] = (
        "thytrader-bar-backtest-v1"
    )
    spread_bps: str | None = None

    @model_validator(mode="after")
    def validate_engine_broker_contract(self) -> Self:
        """Reject assumptions that cannot form one valid immutable research run."""
        _validate_submission_assumptions(self)
        return self


@dataclass(frozen=True, slots=True)
class BacktestSubmissionResult:
    """Immutable run and result identities returned after deterministic publication."""

    run_fingerprint: str
    result_fingerprint: str


class BacktestSubmissionError(RuntimeError):
    """Report a redacted submission failure without granting trading authority."""


@runtime_checkable
class BacktestSubmitter(Protocol):
    """Submit one immutable research run and publish its deterministic result."""

    async def submit(self, request: BacktestSubmissionRequest) -> BacktestSubmissionResult:
        """Return immutable evidence identities for one submitted research simulation."""
        ...


class DisabledBacktestSubmitter:
    """Fail closed until durable submission dependencies are configured."""

    async def submit(self, request: BacktestSubmissionRequest) -> BacktestSubmissionResult:
        """Refuse submission when the authoritative service is unavailable."""
        del request
        raise BacktestSubmissionError("Backtest submission is unavailable.")


class PostgresBacktestSubmitter:
    """Publish/reuse immutable sources and invoke the existing authoritative simulation service."""

    def __init__(self, engine: AsyncEngine, dataset_store: DatasetStore) -> None:
        """Use one application-managed engine and immutable dataset root."""
        self._dataset_store = dataset_store
        self._strategy_store = PostgresStrategyPublicationStore(engine)
        self._run_store = PostgresResearchRunStore(engine)
        self._result_store = PostgresBacktestResultStore(
            engine,
            research_run_store=self._run_store,
            dataset_store=dataset_store,
        )

    async def submit(self, request: BacktestSubmissionRequest) -> BacktestSubmissionResult:
        """Create/reuse exact research inputs, simulate, and return immutable identities."""
        try:
            _validate_submission_assumptions(request)
            strategy = await self._strategy_store.load(request.strategy_fingerprint)
            now = _utc_millisecond(datetime.now(UTC))
            await self._strategy_store.bind_dataset(
                request.strategy_fingerprint,
                request.dataset_fingerprint,
                dataset_store=self._dataset_store,
                bound_at=now,
            )
            execution_fingerprint = _execution_fingerprint(request)
            published_run = await self._run_store.load_by_execution_fingerprint(
                execution_fingerprint,
                dataset_store=self._dataset_store,
            )
            if published_run is None:
                specification = ResearchRunSpecification(
                    schema_version="1.0",
                    run_id=_uuid7(now),
                    created_at=now,
                    strategy_fingerprint=request.strategy_fingerprint,
                    dataset_fingerprint=request.dataset_fingerprint,
                    evaluation=EvaluationWindow(
                        starts_at=request.evaluation_start,
                        ends_at=request.evaluation_end,
                    ),
                    warmup=WarmupWindow(
                        bars=strategy.definition.data_requirements.warmup_bars,
                        starts_at=request.evaluation_start
                        - timedelta(hours=strategy.definition.data_requirements.warmup_bars),
                    ),
                    capital=CapitalAssumptions(
                        quote_currency="USD",
                        initial_quote_balance=request.initial_quote_balance,
                    ),
                    costs=CostAssumptions(
                        maker_fee_rate=request.maker_fee_rate,
                        taker_fee_rate=request.taker_fee_rate,
                        fixed_slippage_bps=request.fixed_slippage_bps,
                    ),
                    broker=_broker_from_request(request),
                    bar_execution=BarExecutionAssumptions(
                        signal_timing="completed_candle_close",
                        fill_timing="next_candle_open",
                    ),
                    engine_contract_version=request.engine_contract_version,
                    random_seed=0,
                )
                published_run = await self._run_store.publish(
                    specification,
                    dataset_store=self._dataset_store,
                    execution_fingerprint=execution_fingerprint,
                )
            result = await evaluate_and_publish_backtest(
                published_run.run_fingerprint,
                run_store=self._run_store,
                strategy_store=self._strategy_store,
                dataset_store=self._dataset_store,
                result_store=self._result_store,
            )
        except Exception as error:
            raise BacktestSubmissionError("Backtest submission is unavailable.") from error
        return BacktestSubmissionResult(
            run_fingerprint=published_run.run_fingerprint,
            result_fingerprint=backtest_result_fingerprint(result),
        )


def _validate_submission_assumptions(request: BacktestSubmissionRequest) -> None:
    """Revalidate every untrusted simulation assumption before source or persistence I/O."""
    _require_valid_broker_inputs(request)
    EvaluationWindow(starts_at=request.evaluation_start, ends_at=request.evaluation_end)
    CapitalAssumptions(
        quote_currency="USD",
        initial_quote_balance=request.initial_quote_balance,
    )
    CostAssumptions(
        maker_fee_rate=request.maker_fee_rate,
        taker_fee_rate=request.taker_fee_rate,
        fixed_slippage_bps=request.fixed_slippage_bps,
    )
    _broker_from_request(request)


def _broker_from_request(request: BacktestSubmissionRequest) -> BrokerAssumptions | None:
    """Resolve V2-only broker inputs, mirroring the CLI contract exactly."""
    if request.engine_contract_version == "thytrader-bar-backtest-v1":
        return None
    if request.spread_bps is None:
        message = "spread_bps is required for the thytrader-bar-backtest-v2 contract"
        raise ValueError(message)
    return BrokerAssumptions(
        price_model="constant_spread_bps",
        spread_bps=request.spread_bps,
        fill_policy="full",
        trigger_evaluation="bid_side",
        equity_marking="bid_close",
    )


def _require_valid_broker_inputs(request: BacktestSubmissionRequest) -> None:
    """Reject mismatched engine and spread combinations before any publication."""
    if request.engine_contract_version == "thytrader-bar-backtest-v1":
        if request.spread_bps is not None:
            raise ValueError("spread_bps requires the thytrader-bar-backtest-v2 contract")
        return
    if request.spread_bps is None:
        raise ValueError("spread_bps is required for the thytrader-bar-backtest-v2 contract")


def _execution_fingerprint(request: BacktestSubmissionRequest) -> str:
    """Hash normalized simulation semantics so equivalent submissions are idempotent."""
    capital = CapitalAssumptions(
        quote_currency="USD",
        initial_quote_balance=request.initial_quote_balance,
    )
    costs = CostAssumptions(
        maker_fee_rate=request.maker_fee_rate,
        taker_fee_rate=request.taker_fee_rate,
        fixed_slippage_bps=request.fixed_slippage_bps,
    )
    broker = _broker_from_request(request)
    payload = {
        "bar_execution": {
            "fill_timing": "next_candle_open",
            "signal_timing": "completed_candle_close",
        },
        "broker": None if broker is None else broker.model_dump(mode="json"),
        "capital": capital.model_dump(mode="json"),
        "costs": costs.model_dump(mode="json"),
        "dataset_fingerprint": request.dataset_fingerprint,
        "engine_contract_version": request.engine_contract_version,
        "evaluation_end": request.evaluation_end.isoformat(),
        "evaluation_start": request.evaluation_start.isoformat(),
        "random_seed": 0,
        "strategy_fingerprint": request.strategy_fingerprint,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{sha256(canonical.encode()).hexdigest()}"


def _utc_millisecond(value: datetime) -> datetime:
    """Normalize a server timestamp to the UUIDv7-representable UTC millisecond."""
    return value.astimezone(UTC).replace(microsecond=(value.microsecond // 1_000) * 1_000)


def _uuid7(created_at: datetime) -> UUID:
    """Create one UUIDv7 whose encoded timestamp matches a UTC millisecond."""
    milliseconds = int(created_at.timestamp() * 1_000)
    value = (
        (milliseconds << 80)
        | (0x7 << 76)
        | (secrets.randbits(12) << 64)
        | (0b10 << 62)
        | secrets.randbits(62)
    )
    return UUID(int=value)
