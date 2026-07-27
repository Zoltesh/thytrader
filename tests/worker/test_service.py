"""Behavioral tests for the continuously running ThyTrader worker."""

import asyncio
import json
import signal
import subprocess
import sys
import time

from thytrader.config import Settings
from thytrader.runtime import RuntimeState
from thytrader.worker.service import run_worker


def test_worker_readiness_tracks_its_running_lifecycle() -> None:
    """Worker readiness should be true only while its run loop is active."""

    async def exercise_worker() -> None:
        """Start and gracefully stop one worker run loop."""
        runtime = RuntimeState(settings=Settings(_env_file=None))
        stop_requested = asyncio.Event()
        task = asyncio.create_task(run_worker(runtime, stop_requested))
        await asyncio.sleep(0)

        assert runtime.ready is True

        stop_requested.set()
        await task

        assert runtime.ready is False

    asyncio.run(exercise_worker())


def test_worker_process_stops_cleanly_on_sigterm() -> None:
    """The worker entry point should handle supervised shutdown cleanly."""
    process = subprocess.Popen(
        [sys.executable, "-m", "thytrader.worker.cli"],
        cwd=".",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        time.sleep(0.25)
        assert process.poll() is None

        process.send_signal(signal.SIGTERM)
        output, _ = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert process.returncode == 0
    payloads = [json.loads(line) for line in output.splitlines()]
    assert [payload["message"] for payload in payloads] == [
        "worker_started",
        "worker_stopped",
    ]
