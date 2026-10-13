"""Prospective input observation around the actual worker's closed-bar invocation."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
import logging
from typing import TYPE_CHECKING

from thytrader.execution.broker import ClientOrderLookup
from thytrader.market_data.models import as_dataset_timeframe, parse_candle_interval
from thytrader.replay.execution import outcome
from thytrader.replay.lookup import observe_broker
from thytrader.replay.models import ReplayPackage, SourceIdentity, canonical_bytes, fingerprint
from thytrader.replay.packages import Manifest, load, publish
from thytrader.replay.source import installed_source_identity
from thytrader.replay.steps import CandleWindow, ReplayStep
from thytrader.replay.timing import TimingObservation
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.runtime_inputs import InputTape, runtime_input_scope
from thytrader.strategies.models import reference_instruments
from thytrader.trading.entry_latch import process_entry_inhibited
from thytrader.trading.models import DeploymentMode

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
    from decimal import Decimal
    from pathlib import Path

    from thytrader.exchanges.fees import FeeProfile
    from thytrader.execution.broker import Broker
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.trading.models import DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore

_logger = logging.getLogger(__name__)
CAPTURE_WRITE_TIMEOUT_SECONDS = 2


class FileCapture:
    """Explicit opt-in private capture root with hard file-count and byte budgets."""

    def __init__(
        self, root: Path, *, max_packages: int = 256, max_bytes: int = 64 * 1024 * 1024
    ) -> None:
        """No directories are created until an eligible observation is published."""
        self.root = root
        self.max_packages = max_packages
        self.max_bytes = max_bytes
        self.failure_count = 0
        self.disabled = False

    def write(self, package: ReplayPackage) -> None:
        """Refuse budget exhaustion rather than silently overwrite or rewrite prior evidence."""
        if self.disabled:
            raise ValueError("capture is disabled after an unavailable write")
        content = canonical_bytes(package)
        digest = fingerprint(content)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        claim = self.root / ".capture-lock"
        try:
            claim.mkdir(mode=0o700)
        except FileExistsError as error:
            raise ValueError("capture root already has an active or interrupted writer") from error
        try:
            self._publish_with_budget(package, content, digest)
        finally:
            claim.rmdir()

    def _publish_with_budget(self, package: ReplayPackage, content: bytes, digest: str) -> None:
        """Hold a root-exclusive claim across durable budget inspection and publication."""
        existing = self.root / digest.removeprefix("sha256:") / "manifest.json"
        if existing.exists():
            checked, _manifest = load(existing)
            if canonical_bytes(checked) != content:
                raise ValueError("capture immutable content collision")
            return
        manifests = list(self.root.glob("*/manifest.json")) if self.root.exists() else []
        if len(manifests) >= self.max_packages:
            raise ValueError("capture package-count budget exhausted")
        manifest = Manifest(
            schema_version="thytrader-replay-manifest-v1",
            fingerprint=digest,
            payload="evidence.json",
            size=len(content),
        )
        size = sum(path.stat().st_size for path in self.root.glob("*/*.json"))
        if size + len(content) + len(manifest.model_dump_json().encode()) > self.max_bytes:
            raise ValueError("capture byte budget exhausted")
        publish(self.root, package)


_CAPTURE: ContextVar[FileCapture | None] = ContextVar("execution_replay_capture", default=None)


@contextmanager
def capture_scope(sink: FileCapture | None) -> Iterator[None]:
    """Bind observation for the real worker without granting broker or database authority."""
    token = _CAPTURE.set(sink)
    try:
        yield
    finally:
        _CAPTURE.reset(token)


async def captured_closed_bar(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    product: MarketProduct,
    candles: Sequence[Candle],
    broker: Broker,
    store: ExecutionStore,
    process: Callable[..., Awaitable[DeploymentSnapshot]],
    risk_policy: RiskPolicyDefinition | None = None,
    portfolio: Sequence[DeploymentSnapshot] = (),
    htf_candles: Sequence[Candle] = (),
    indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None = None,
    reference_candles: Mapping[str, Sequence[Candle]] | None = None,
    live_base_available: Decimal | None = None,
    marks: Mapping[str, Decimal] | None = None,
    fee_profile: FeeProfile | None = None,
    allow_new_entries: bool = True,
) -> DeploymentSnapshot:
    """Observe the existing live invocation; persistence failure never repeats a broker operation.

    PaperBroker identity controls immediate-fill semantics, so it is never wrapped.
    The initial book is observed after ordinary worker reconciliation/quote preparation;
    this slice never claims to capture that preceding stage retrospectively.
    """
    sink = _CAPTURE.get()
    enabled = (
        sink is not None and not sink.disabled and snapshot.deployment.mode is DeploymentMode.LIVE
    )
    recorded = observe_broker(broker) if enabled else None
    tape = InputTape()
    available = datetime.now(UTC)
    entry_inhibited = process_entry_inhibited(snapshot.deployment.mode)

    async def advance() -> DeploymentSnapshot:
        """Call exactly the same closed-bar orchestrator and arguments as the worker did."""
        return await process(
            snapshot,
            strategy=strategy,
            product=product,
            candles=candles,
            broker=recorded if recorded is not None else broker,
            store=store,
            risk_policy=risk_policy,
            portfolio=portfolio,
            htf_candles=htf_candles,
            indicator_timeframe_candles=indicator_timeframe_candles,
            reference_candles=reference_candles,
            live_base_available=live_base_available,
            marks=marks,
            fee_profile=fee_profile,
            allow_new_entries=allow_new_entries,
        )

    started = datetime.now(UTC)
    if enabled:
        timing = TimingObservation()
        with runtime_input_scope(tape):
            after = await advance()
        timing.stop()
    else:
        return await advance()
    completed = datetime.now(UTC)
    if sink is None or recorded is None:
        return after
    if tape.failed or recorded.failed:
        sink.failure_count += 1
        _logger.warning(
            "execution_replay_capture_failed type=buffer_exhausted trading_unchanged=true"
        )
        return after
    try:
        references = {item.id: item for item in reference_instruments(strategy)}
        step = ReplayStep(
            operation="closed_bar",
            before=snapshot,
            product=product,
            candles=tuple(candles),
            data_available_at=available,
            evaluation_started_at=started,
            decision_completed_at=completed,
            timing=timing.finish(),
            htf_candles=tuple(htf_candles),
            indicator_windows=tuple(
                CandleWindow(
                    key=key,
                    product_id=product.product_id,
                    timeframe=as_dataset_timeframe(parse_candle_interval(key)),
                    candles=tuple(rows),
                )
                for key, rows in (indicator_timeframe_candles or {}).items()
            ),
            reference_windows=tuple(
                CandleWindow(
                    key=key,
                    product_id=references[key].product_id,
                    timeframe=references[key].timeframe,
                    candles=tuple(rows),
                )
                for key, rows in (reference_candles or {}).items()
            ),
            portfolio=tuple(portfolio),
            marks=tuple((marks or {}).items()),
            live_base_available=live_base_available,
            fee_profile=fee_profile,
            risk_policy=risk_policy or compiled_default_risk_policy(),
            allow_new_entries=allow_new_entries,
            entry_inhibited=entry_inhibited,
            runtime_inputs=tuple(tape.events),
            broker_events=tuple(recorded.events),
            broker_supports_client_lookup=isinstance(broker, ClientOrderLookup),
            expected=outcome(after),
        )
        package = ReplayPackage(
            schema_version="thytrader-replay-v1",
            evidence_kind="prospective",
            source=SourceIdentity(execution_build=installed_source_identity(), research_build=None),
            strategy=strategy,
            original_strategy=None,
            steps=(step,),
            ledger=None,
            unavailable_reasons=(
                "ORIGINAL_RESEARCH_MISSING",
                "ORIGINAL_RESEARCH_BUILD_MISSING",
                "PRE_BAR_RECONCILIATION_NOT_CAPTURED",
                "DYNAMIC_RISK_READS_NOT_CAPTURED",
            ),
        )
        async with asyncio.timeout(CAPTURE_WRITE_TIMEOUT_SECONDS):
            await asyncio.to_thread(sink.write, package)
    except TimeoutError:
        # to_thread cannot cancel filesystem work: permit at most this one outstanding write.
        sink.disabled = True
        sink.failure_count += 1
        _logger.warning(
            "execution_replay_capture_timeout outstanding_write_may_finish=true "
            "capture_disabled=true"
        )
    except Exception as error:
        sink.failure_count += 1
        _logger.exception(
            "execution_replay_capture_failed type=%s trading_unchanged=true",
            type(error).__name__,
            exc_info=(RuntimeError, RuntimeError("Capture unavailable; trading unchanged"), None),
        )
    return after
