"""Capture budgets are durable, idempotent and bounded even when a thread outlives its awaiter."""

from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Event

import pytest

from tests.replay.generate_fixtures import LifecycleVenue
from thytrader.execution.loop import process_closed_bar
from thytrader.replay.capture import FileCapture, capture_scope, captured_closed_bar
from thytrader.replay.execution import seed_store
from thytrader.replay.models import ReplayPackage, canonical_bytes
from thytrader.replay.packages import read_input


def test_capture_budget_allows_same_content_retry(tmp_path: Path) -> None:
    """A replayed publication does not consume another file-count or byte reservation."""
    package = read_input(Path("tests/replay/fixtures/partial-loss.input.json"))
    sink = FileCapture(tmp_path / "capture", max_packages=1)
    sink.write(package)
    sink.write(package)
    assert len(list(sink.root.glob("*/manifest.json"))) == 1


def test_capture_byte_budget_includes_publication_manifest(tmp_path: Path) -> None:
    """The documented durable-byte ceiling includes metadata, not just evidence bytes."""
    package = read_input(Path("tests/replay/fixtures/partial-loss.input.json"))
    sink = FileCapture(tmp_path / "capture", max_bytes=len(canonical_bytes(package)))
    with pytest.raises(ValueError, match="budget"):
        sink.write(package)
    assert not list(sink.root.glob("*/manifest.json"))


def test_capture_existing_budget_survives_sink_reconstruction(tmp_path: Path) -> None:
    """Restart does not reset durable storage budgets to an empty in-memory counter."""
    root = tmp_path / "capture"
    sink = FileCapture(root, max_packages=1)
    sink.write(read_input(Path("tests/replay/fixtures/partial-loss.input.json")))
    next_sink = FileCapture(root, max_packages=1)
    with pytest.raises(ValueError, match="budget"):
        next_sink.write(read_input(Path("tests/replay/fixtures/no-signal.input.json")))


def test_capture_disablement_refuses_future_write_work(tmp_path: Path) -> None:
    """A timed-out writer may finish once; its sink cannot enqueue additional writes forever."""
    sink = FileCapture(tmp_path / "capture")
    sink.disabled = True
    with pytest.raises(ValueError, match="disabled"):
        sink.write(read_input(Path("tests/replay/fixtures/partial-loss.input.json")))
    assert not sink.root.exists()


def test_capture_root_exclusive_claim_prevents_second_writer(tmp_path: Path) -> None:
    """A private root has one publication writer, including separate sink objects."""
    root = tmp_path / "capture"
    root.mkdir()
    (root / ".capture-lock").mkdir()
    sink = FileCapture(root)
    with pytest.raises(ValueError, match="writer"):
        sink.write(read_input(Path("tests/replay/fixtures/partial-loss.input.json")))
    assert not list(root.glob("*/manifest.json"))


@pytest.mark.anyio
async def test_timeout_disables_further_capture_without_repeating_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """to_thread may finish once after timeout, but subsequent worker calls enqueue no writes."""
    monkeypatch.setattr("thytrader.replay.capture.CAPTURE_WRITE_TIMEOUT_SECONDS", 0.5)
    entered, release, completed = Event(), Event(), Event()

    class SlowSink(FileCapture):
        """Block one publication after its exclusive root claim has been acquired."""

        def _publish_with_budget(self, package: ReplayPackage, content: bytes, digest: str) -> None:
            """Permit the existing write to finish after the awaiter's timeout."""
            entered.set()
            try:
                assert release.wait(5), "bounded synthetic writer was not released"
                super()._publish_with_budget(package, content, digest)
            finally:
                completed.set()

    package = read_input(Path("tests/replay/fixtures/usdc-lifecycle.input.json"))
    assert package.strategy is not None
    step = package.steps[0]
    store = await seed_store(step.before)
    venue = LifecycleVenue()
    sink = SlowSink(tmp_path / "capture")
    try:
        with capture_scope(sink):
            after = await captured_closed_bar(
                step.before,
                strategy=package.strategy,
                product=step.product,
                candles=step.candles,
                broker=venue,
                store=store,
                process=process_closed_bar,
                risk_policy=step.risk_policy,
                htf_candles=step.htf_candles,
                indicator_timeframe_candles={w.key: w.candles for w in step.indicator_windows},
                reference_candles={w.key: w.candles for w in step.reference_windows},
                fee_profile=step.fee_profile,
            )
            assert entered.is_set() and sink.disabled and sink.failure_count == 1
            assert venue.count == 1
            await captured_closed_bar(
                after,
                strategy=package.strategy,
                product=step.product,
                candles=step.candles,
                broker=venue,
                store=store,
                process=process_closed_bar,
                allow_new_entries=False,
            )
            assert venue.count == 1 and sink.failure_count == 1
    finally:
        release.set()
        assert await asyncio.to_thread(completed.wait, 5)
    assert len(list(sink.root.glob("*/manifest.json"))) == 1
