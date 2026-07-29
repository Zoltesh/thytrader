"""Real process lifecycle tests for the dedicated market-data worker boundary."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import time

_REPOSITORY_ROOT = Path(__file__).parents[2]


def test_market_data_worker_loop_publishes_and_stops_on_sigterm(tmp_path: Path) -> None:
    """A real child process becomes ready, publishes work, and shuts down cleanly."""
    dataset_root = tmp_path / "datasets"
    readiness_file = tmp_path / "ready"
    environment = os.environ.copy()
    environment.update(
        {
            "TEST_DATASET_ROOT": str(dataset_root),
            "TEST_READINESS_FILE": str(readiness_file),
        }
    )
    code = """
import asyncio
from datetime import UTC, datetime
import os
from pathlib import Path
import signal

from thytrader.market_data.datasets import DatasetStore
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.market_data.worker_state import InMemoryMarketDataWorkerStateStore
from thytrader.market_data_worker.service import run_market_data_worker

async def main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stop.set)
    ready_path = Path(os.environ["TEST_READINESS_FILE"])
    def readiness(ready: bool) -> None:
        if ready:
            ready_path.touch()
        elif ready_path.exists():
            ready_path.unlink()
    await run_market_data_worker(
        stop,
        service=MarketDataService(DemoMarketData()),
        dataset_store=DatasetStore(Path(os.environ["TEST_DATASET_ROOT"])),
        state_store=InMemoryMarketDataWorkerStateStore(),
        provider="demo",
        product_id="BTC-USD",
        lookback_hours=3,
        interval_seconds=60,
        now_factory=lambda: datetime.now(UTC),
        on_readiness_changed=readiness,
    )

asyncio.run(main())
"""
    process = subprocess.Popen(  # noqa: S603 - fixed interpreter and in-repository test program.
        [sys.executable, "-c", code],
        cwd=_REPOSITORY_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            manifests = tuple((dataset_root / "manifests").glob("*.json"))
            if readiness_file.exists() and len(manifests) == 1:
                break
            time.sleep(0.05)
        if process.poll() is not None:
            output, _ = process.communicate(timeout=1)
            raise AssertionError(output)
        assert readiness_file.is_file()
        assert len(tuple((dataset_root / "manifests").glob("*.json"))) == 1

        process.send_signal(signal.SIGTERM)
        output, _ = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert process.returncode == 0, output
    assert not readiness_file.exists()


def test_market_data_worker_installed_entry_point_exists() -> None:
    """Packaging must install the separately supervised worker executable."""
    executable = Path(sys.executable).parent / "thytrader-market-data-worker"
    assert executable.is_file()
