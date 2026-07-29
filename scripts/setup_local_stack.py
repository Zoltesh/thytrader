#!/usr/bin/env python3
"""Bootstrap ThyTrader's complete local Compose stack for a fresh clone.

The helper preserves unrelated ignored ``.env`` settings, configures matching
local-only PostgreSQL values, applies migrations as a one-shot Compose service,
and starts PostgreSQL, API, portfolio-history worker, market-data worker, and
web together. It never prints credentials or database URLs.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from typing import TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:
    from collections.abc import Callable

_DATABASE_URL_KEY = "THYTRADER_DATABASE_URL"
_COMPOSE_DATABASE_URL_KEY = "THYTRADER_COMPOSE_DATABASE_URL"
_DATABASE_PASSWORD_KEY = "THYTRADER_PG_PASSWORD"  # noqa: S105 - dotenv variable name.
_DATABASE_USER_KEY = "THYTRADER_PG_USER"
_DATABASE_NAME_KEY = "THYTRADER_PG_DB"
_DEFAULT_DATABASE_USER = "thytrader"
_DEFAULT_DATABASE_NAME = "thytrader"
_DEFAULT_DATABASE_PASSWORD = "thytrader-local-development-only"  # noqa: S105 - local-only fallback.
_DATABASE_HOST_PORT = 5433


def _read_environment(path: Path) -> dict[str, str]:
    """Read simple ``KEY=VALUE`` entries from an optional dotenv file."""
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped_line = line.strip()
        if not stripped_line or stripped_line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        values[key.strip()] = value.strip()
    return values


def _replace_or_append(lines: list[str], key: str, value: str) -> list[str]:
    """Set one dotenv value while preserving comments and unrelated entries."""
    replacement = f"{key}={value}"
    prefix = f"{key}="
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = replacement
            return lines
    lines.append(replacement)
    return lines


def configure_local_database(
    environment_path: Path,
    password_factory: Callable[[], str] = lambda: _DEFAULT_DATABASE_PASSWORD,
) -> None:
    """Create matching host and Compose database settings in ``.env``.

    Existing non-database configuration remains unchanged. An existing custom
    database URL is rejected rather than risking a mismatched local stack.
    """
    values = _read_environment(environment_path)
    existing_url = values.get(_DATABASE_URL_KEY, "").strip()
    existing_password = values.get(_DATABASE_PASSWORD_KEY, "").strip()

    user = values.get(_DATABASE_USER_KEY, _DEFAULT_DATABASE_USER).strip() or _DEFAULT_DATABASE_USER
    database_name = (
        values.get(_DATABASE_NAME_KEY, _DEFAULT_DATABASE_NAME).strip() or _DEFAULT_DATABASE_NAME
    )
    password = existing_password or password_factory()
    encoded_user = quote(user, safe="")
    encoded_password = quote(password, safe="")
    encoded_database_name = quote(database_name, safe="")
    database_url = (
        "postgresql+asyncpg://"
        f"{encoded_user}:{encoded_password}@127.0.0.1:{_DATABASE_HOST_PORT}/"
        f"{encoded_database_name}"
    )
    compose_database_url = (
        "postgresql+asyncpg://"
        f"{encoded_user}:{encoded_password}@postgres:5432/{encoded_database_name}"
    )
    if existing_url and existing_url != database_url:
        message = (
            "Existing THYTRADER_DATABASE_URL differs; refuse to overwrite "
            "local database configuration."
        )
        raise ValueError(message)

    lines = (
        environment_path.read_text(encoding="utf-8").splitlines()
        if environment_path.exists()
        else []
    )
    for key, value in (
        (_DATABASE_USER_KEY, user),
        (_DATABASE_PASSWORD_KEY, password),
        (_DATABASE_NAME_KEY, database_name),
        (_DATABASE_URL_KEY, database_url),
        (_COMPOSE_DATABASE_URL_KEY, compose_database_url),
    ):
        lines = _replace_or_append(lines, key, value)

    environment_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run(command: list[str], project_root: Path) -> None:
    """Run one fixed bootstrap command without embedding credentials in arguments."""
    subprocess.run(command, check=True, cwd=project_root)  # noqa: S603 - fixed internal commands.


def _stack_commands() -> tuple[list[str], ...]:
    """Return the ordered commands that build, migrate, and start the stack."""
    return (
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


def main() -> int:
    """Configure and start the full local ThyTrader stack."""
    project_root = Path(__file__).resolve().parent.parent
    environment_path = project_root / ".env"

    try:
        commands = _stack_commands()
        _run(commands[0], project_root)
        configure_local_database(environment_path)
        _run(commands[1], project_root)
        _run(commands[2], project_root)
    except FileNotFoundError:
        print(  # noqa: T201 - CLI status message.
            "Docker Compose is required. Install Docker Desktop or Docker Engine with Compose.",
            file=sys.stderr,
        )
        return 1
    except (subprocess.CalledProcessError, ValueError) as error:
        print(f"Local ThyTrader setup failed: {error}", file=sys.stderr)  # noqa: T201 - CLI error.
        return 1

    print("ThyTrader is ready at http://127.0.0.1:5175.")  # noqa: T201 - CLI status.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
