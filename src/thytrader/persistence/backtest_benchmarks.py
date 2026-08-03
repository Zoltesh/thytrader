"""Read-only derived benchmark comparisons for immutable backtest results."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from thytrader.backtest.benchmark import (
    BacktestBenchmarkError,
    calculate_buy_and_hold_benchmark,
)
from thytrader.market_data.datasets import DatasetStoreError
from thytrader.persistence.backtest_results import (
    BacktestResultIntegrityError,
    BacktestResultNotFoundError,
    BacktestResultUnavailableError,
)
from thytrader.persistence.postgres_backtests import BacktestPublicationError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.backtest.models import BacktestBenchmark, BacktestResult
    from thytrader.market_data.models import Candle
    from thytrader.research.models import ResearchRunSpecification


class BacktestBenchmarkNotFoundError(LookupError):
    """Signal that the requested source backtest result does not exist."""


class BacktestBenchmarkUnavailableError(RuntimeError):
    """Signal that benchmark source artifacts are disabled or unavailable."""


class BacktestBenchmarkIntegrityError(RuntimeError):
    """Signal that a derived benchmark failed source or arithmetic verification."""


class BacktestSingleResultReader(Protocol):
    """Read one fully reverified immutable result by content identity."""

    async def load(self, result_fingerprint: str) -> BacktestResult:
        """Load one result or raise its controlled persistence error."""
        ...


class BacktestDatasetReader(Protocol):
    """Read one verified immutable candle dataset by content identity."""

    def load_candles(self, content_fingerprint: str) -> Sequence[Candle]:
        """Load verified candles for one published dataset fingerprint."""
        ...


class BacktestSourceReader(Protocol):
    """Read the verified immutable research run behind one backtest result."""

    async def load_source_specification(self, result: BacktestResult) -> ResearchRunSpecification:
        """Load and reverify the exact source specification for a result."""
        ...


@runtime_checkable
class BacktestBenchmarkReader(Protocol):
    """Read one deterministic benchmark comparison without mutation authority."""

    async def load(self, result_fingerprint: str) -> BacktestBenchmark:
        """Load a derived benchmark by its source result fingerprint."""
        ...


class DisabledBacktestBenchmarkReader:
    """Fail closed when verified dataset storage is not configured."""

    async def load(self, result_fingerprint: str) -> BacktestBenchmark:
        """Reject benchmark inspection rather than fabricating an unavailable comparison."""
        del result_fingerprint
        raise BacktestBenchmarkUnavailableError("Backtest benchmark is unavailable.")


class PostgresBacktestBenchmarkReader:
    """Derive benchmarks from a reverified PostgreSQL result and immutable candle dataset."""

    def __init__(
        self,
        result_reader: BacktestSingleResultReader,
        source_reader: BacktestSourceReader,
        dataset_store: BacktestDatasetReader,
    ) -> None:
        """Bind the result, source-run, and verified dataset read boundaries."""
        self._result_reader = result_reader
        self._source_reader = source_reader
        self._dataset_store = dataset_store

    async def load(self, result_fingerprint: str) -> BacktestBenchmark:
        """Load and derive one benchmark only from exact verified immutable inputs."""
        try:
            result = await self._result_reader.load(result_fingerprint)
            specification = await self._source_reader.load_source_specification(result)
            candles = self._dataset_store.load_candles(specification.dataset_fingerprint)
            return calculate_buy_and_hold_benchmark(result, specification, candles)
        except BacktestResultNotFoundError as error:
            raise BacktestBenchmarkNotFoundError(
                "Published backtest result was not found."
            ) from error
        except BacktestBenchmarkError as error:
            raise BacktestBenchmarkIntegrityError(
                "Backtest benchmark source or arithmetic verification failed."
            ) from error
        except (
            BacktestResultIntegrityError,
            BacktestResultUnavailableError,
            BacktestPublicationError,
            DatasetStoreError,
        ) as error:
            raise BacktestBenchmarkUnavailableError(
                "Backtest benchmark source verification is unavailable."
            ) from error
