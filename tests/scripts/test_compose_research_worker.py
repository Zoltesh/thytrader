"""Compose configuration of the research-worker service (ADR 0092)."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from typing import cast

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]

type Mapping = dict[str, object]


def _services() -> dict[str, Mapping]:
    """Parse compose.yaml and return its services."""
    document = cast("Mapping", yaml.safe_load((_ROOT / "compose.yaml").read_text("utf-8")))
    return cast("dict[str, Mapping]", document["services"])


def test_research_worker_runs_the_pool_without_credentials_and_read_only_data() -> None:
    """The service runs the pool CLI, reads datasets read-only, and never sees Coinbase keys."""
    service = _services()["research-worker"]
    assert service["command"] == ["/app/.venv/bin/thytrader-research-worker"]
    environment = cast("Mapping", service["environment"])
    assert not any("COINBASE" in key for key in environment)
    assert "THYTRADER_SETTINGS_FILE" not in environment
    assert environment["THYTRADER_MARKET_DATA_DATASET_ROOT"] == "/var/lib/thytrader/market-data"
    assert environment["THYTRADER_RESEARCH_WORKER_COUNT"] == "${THYTRADER_RESEARCH_WORKER_COUNT:-2}"
    assert str(environment["THYTRADER_DATABASE_URL"]).startswith("${THYTRADER_COMPOSE_DATABASE_URL")
    assert service["volumes"] == ["thytrader_market_data:/var/lib/thytrader/market-data:ro"]
    assert "ports" not in service
    depends = cast("Mapping", service["depends_on"])
    assert depends == {"migrate": {"condition": "service_completed_successfully"}}
    assert service["restart"] == "unless-stopped"
    assert int(cast("int", service["oom_score_adj"])) > 0
    readiness = str(environment["THYTRADER_RESEARCH_WORKER_READINESS_FILE"])
    assert readiness in str(cast("Mapping", service["healthcheck"])["test"])


def test_api_keeps_its_service_and_no_service_publishes_research_ports() -> None:
    """Research moved out of the API: the API service is unchanged and loopback-bound."""
    services = _services()
    assert {"api", "worker", "market-data-worker", "execution-worker", "research-worker"} <= set(
        services
    )
    for name, service in services.items():
        for port in cast("list[str]", service.get("ports", [])):
            assert port.startswith("127.0.0.1:"), (name, port)


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI is not installed")
def test_compose_file_validates_with_docker_compose(tmp_path: Path) -> None:
    """``docker compose config --quiet`` accepts the file (an empty env file, no output)."""
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("", encoding="utf-8")
    result = subprocess.run(  # noqa: S603 - fixed CLI arguments, no shell.
        [  # noqa: S607 - docker is resolved from PATH on purpose.
            "docker",
            "compose",
            "--project-directory",
            str(_ROOT),
            "--env-file",
            str(empty_env),
            "-f",
            str(_ROOT / "compose.yaml"),
            "config",
            "--quiet",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env={
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", str(tmp_path)),
        },
    )
    if "is not a docker command" in result.stderr:
        pytest.skip("docker compose plugin is not installed")
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
