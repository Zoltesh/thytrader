#!/usr/bin/env python3
"""Bootstrap ThyTrader's local PostgreSQL history service for a new clone.

The script creates matching local-only configuration in the ignored ``.env``
file, starts the repository's loopback-bound Compose service, waits for
readiness, and explicitly applies Alembic migrations. It never prints the
credential or connection URL.
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
_DATABASE_PASSWORD_KEY = "THYTRADER_PG_PASSWORD"  # noqa: S105 - dotenv variable name.
_DATABASE_USER_KEY = "THYTRADER_PG_USER"
_DATABASE_NAME_KEY = "THYTRADER_PG_DB"
_DEFAULT_DATABASE_USER = "thytrader"
_DEFAULT_DATABASE_NAME = "thytrader"
_DEFAULT_DATABASE_PASSWORD = "thytrader-local-development-only"  # noqa: S105 - local-only Compose fallback.
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
    """Create matching Compose and application database settings in ``.env``.

    Existing non-database configuration remains unchanged. An existing custom
    database URL is intentionally rejected rather than risk overwriting or
    starting a container with mismatched credentials.
    """
    values = _read_environment(environment_path)
    existing_url = values.get(_DATABASE_URL_KEY, "").strip()
    existing_password = values.get(_DATABASE_PASSWORD_KEY, "").strip()

    user = values.get(_DATABASE_USER_KEY, _DEFAULT_DATABASE_USER).strip() or _DEFAULT_DATABASE_USER
    database_name = (
        values.get(_DATABASE_NAME_KEY, _DEFAULT_DATABASE_NAME).strip() or _DEFAULT_DATABASE_NAME
    )
    password = existing_password or password_factory()
    database_url = (
        "postgresql+asyncpg://"
        f"{quote(user, safe='')}:{quote(password, safe='')}@127.0.0.1:{_DATABASE_HOST_PORT}/"
        f"{quote(database_name, safe='')}"
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
    ):
        lines = _replace_or_append(lines, key, value)

    environment_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run(command: list[str], project_root: Path) -> None:
    """Run one bootstrap command without embedding credentials in its arguments."""
    subprocess.run(command, check=True, cwd=project_root)  # noqa: S603 - fixed internal commands.


def main() -> int:
    """Configure and initialize the optional local portfolio-history database."""
    project_root = Path(__file__).resolve().parent.parent
    environment_path = project_root / ".env"

    try:
        _run(["docker", "compose", "version"], project_root)
        configure_local_database(environment_path)
        _run(["docker", "compose", "up", "-d", "--wait", "postgres"], project_root)
        _run(["uv", "run", "alembic", "upgrade", "head"], project_root)
    except FileNotFoundError:
        print(  # noqa: T201 - CLI status message.
            "Docker Compose is required. Install Docker Desktop or Docker Engine with Compose.",
            file=sys.stderr,
        )
        return 1
    except (subprocess.CalledProcessError, ValueError) as error:
        print(f"Local PostgreSQL setup failed: {error}", file=sys.stderr)  # noqa: T201 - CLI error.
        return 1

    print("Local PostgreSQL is ready. Portfolio snapshots will persist on refresh.")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
