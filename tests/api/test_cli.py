"""Integration tests for the ThyTrader API process entry point."""

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

_REPOSITORY_ROOT = Path(__file__).parents[2]


def _available_loopback_port() -> int:
    """Reserve and release an available loopback port for a process test."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def test_api_process_serves_liveness_until_sigterm() -> None:
    """The API entry point should serve health traffic and stop cleanly."""
    port = _available_loopback_port()
    environment = os.environ.copy()
    environment.update(
        {
            "THYTRADER_API_HOST": "127.0.0.1",
            "THYTRADER_API_PORT": str(port),
            "THYTRADER_ENVIRONMENT": "test",
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "thytrader.api.cli"],
        cwd=_REPOSITORY_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    response_payload: dict[str, str] | None = None
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                with urlopen(f"http://127.0.0.1:{port}/health/live", timeout=0.25) as response:
                    response_payload = json.load(response)
                    break
            except URLError:
                time.sleep(0.05)

        assert response_payload == {
            "service": "api",
            "status": "ok",
            "version": "0.1.0",
        }

        process.send_signal(signal.SIGTERM)
        output, _ = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    # Uvicorn completes graceful shutdown, then re-raises the signal for supervisors.
    assert process.returncode == -signal.SIGTERM
    messages = [json.loads(line)["message"] for line in output.splitlines()]
    assert "Shutting down" in messages
    assert any(message.startswith("Finished server process") for message in messages)
