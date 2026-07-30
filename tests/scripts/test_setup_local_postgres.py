"""Tests for the clone-friendly local PostgreSQL bootstrap helper."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType


_SCRIPT_PATH = Path("scripts/setup_local_stack.py")


def _load_script() -> ModuleType:
    """Load the standalone bootstrap helper without executing its CLI entrypoint."""
    specification = importlib.util.spec_from_file_location("setup_local_stack", _SCRIPT_PATH)
    if specification is None or specification.loader is None:
        message = "Could not load the local PostgreSQL bootstrap helper."
        raise AssertionError(message)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_configure_local_database_creates_matching_ignored_environment(
    tmp_path: Path,
) -> None:
    """Bootstrap must create a local URL whose password matches Compose configuration."""
    module = _load_script()
    environment_path = tmp_path / ".env"

    module.configure_local_database(environment_path, password_factory=lambda: "test-password")

    content = environment_path.read_text(encoding="utf-8")
    assert "THYTRADER_PG_PASSWORD=test-password" in content
    assert "THYTRADER_DATABASE_URL=" in content
    assert "THYTRADER_COMPOSE_DATABASE_URL=" in content
    assert "test-password" in content


def test_configure_local_database_preserves_existing_environment_values(
    tmp_path: Path,
) -> None:
    """Bootstrap must not discard unrelated local API and Coinbase configuration."""
    module = _load_script()
    environment_path = tmp_path / ".env"
    environment_path.write_text("THYTRADER_API_PORT=8200\n", encoding="utf-8")

    module.configure_local_database(environment_path, password_factory=lambda: "test-password")

    content = environment_path.read_text(encoding="utf-8")
    assert "THYTRADER_API_PORT=8200" in content
    assert "THYTRADER_PG_PASSWORD=test-password" in content


def test_configure_local_database_refuses_to_replace_a_custom_database_url(
    tmp_path: Path,
) -> None:
    """Bootstrap must not redirect a configured user-managed database to Compose."""
    module = _load_script()
    environment_path = tmp_path / ".env"
    environment_path.write_text(
        "THYTRADER_DATABASE_URL=postgresql+asyncpg://custom@db.example/history\n"
        "THYTRADER_PG_PASSWORD=test-password\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="refuse to overwrite"):
        module.configure_local_database(environment_path)


def test_configure_local_database_migrates_the_generated_legacy_host_port(
    tmp_path: Path,
) -> None:
    """Bootstrap must safely update its own former 5433 host-port default."""
    module = _load_script()
    environment_path = tmp_path / ".env"
    environment_path.write_text(
        "THYTRADER_API_PORT=8200\n"
        "THYTRADER_PG_USER=thytrader\n"
        "THYTRADER_PG_PASSWORD=test-password\n"
        "THYTRADER_PG_DB=thytrader\n"
        "THYTRADER_DATABASE_URL="
        "postgresql+asyncpg://thytrader:test-password@127.0.0.1:5433/thytrader\n"
        "THYTRADER_COMPOSE_DATABASE_URL="
        "postgresql+asyncpg://thytrader:test-password@postgres:5432/thytrader\n",
        encoding="utf-8",
    )

    module.configure_local_database(environment_path)

    content = environment_path.read_text(encoding="utf-8")
    assert "@127.0.0.1:5439/thytrader" in content
    assert "@127.0.0.1:5433/thytrader" not in content
    assert "THYTRADER_API_PORT=8200" in content


def test_configure_local_database_rejects_duplicate_database_url_keys(tmp_path: Path) -> None:
    """Ambiguous duplicate managed keys must not be partially rewritten."""
    module = _load_script()
    environment_path = tmp_path / ".env"
    original = (
        "THYTRADER_PG_USER=thytrader\n"
        "THYTRADER_PG_PASSWORD=test-password\n"
        "THYTRADER_PG_DB=thytrader\n"
        "THYTRADER_DATABASE_URL=postgresql+asyncpg://custom@db.example/portfolio\n"
        "THYTRADER_DATABASE_URL="
        "postgresql+asyncpg://thytrader:test-password@127.0.0.1:5433/thytrader\n"
    )
    environment_path.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate THYTRADER_DATABASE_URL"):
        module.configure_local_database(environment_path)

    assert environment_path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    "database_line",
    (
        " THYTRADER_DATABASE_URL=postgresql+asyncpg://thytrader:test-password@127.0.0.1:5433/thytrader",
        " THYTRADER_DATABASE_URL =postgresql+asyncpg://thytrader:test-password@127.0.0.1:5433/thytrader",
    ),
)
def test_configure_local_database_rewrites_whitespace_normalized_legacy_key(
    tmp_path: Path,
    database_line: str,
) -> None:
    """Reading and replacing managed keys must use the same normalization rules."""
    module = _load_script()
    environment_path = tmp_path / ".env"
    environment_path.write_text(
        "THYTRADER_PG_USER=thytrader\n"
        "THYTRADER_PG_PASSWORD=test-password\n"
        "THYTRADER_PG_DB=thytrader\n"
        f"{database_line}\n",
        encoding="utf-8",
    )

    module.configure_local_database(environment_path)

    content = environment_path.read_text(encoding="utf-8")
    assert content.count("THYTRADER_DATABASE_URL") == 1
    assert "@127.0.0.1:5433/thytrader" not in content
    assert "@127.0.0.1:5439/thytrader" in content


def test_stack_commands_migrate_before_starting_long_running_services() -> None:
    """The one-command path must make migration a gate before API and worker startup."""
    module = _load_script()

    commands = module._stack_commands()

    assert commands == (
        ["docker", "compose", "version"],
        ["docker", "compose", "up", "--build", "migrate"],
        [
            "docker",
            "compose",
            "up",
            "-d",
            "--build",
            "--wait",
            "api",
            "worker",
            "market-data-worker",
            "web",
        ],
    )
