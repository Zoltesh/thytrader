"""Timing excludes evidence preparation and preserves missing-I/O instrumentation explicitly."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.replay import timing

if TYPE_CHECKING:
    from pytest import MonkeyPatch


def test_decision_timing_stops_before_evidence_preparation(monkeypatch: MonkeyPatch) -> None:
    """Late serialization must not be reported as decision computation or venue latency."""
    elapsed = iter((100, 200, 9000))
    cpu = iter((50, 80, 9000))
    monkeypatch.setattr(timing, "perf_counter_ns", lambda: next(elapsed))
    monkeypatch.setattr(timing, "process_time_ns", lambda: next(cpu))
    observed = timing.TimingObservation()
    assert hasattr(observed, "stop"), "missing pinned decision boundary"
    observed.stop()
    report = observed.finish()
    assert report.elapsed_ns == 100
    assert report.computation_cpu_ns == 30
    assert report.queue_wait_ns is None
    assert report.database_calls is None
