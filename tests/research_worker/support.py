"""Shared PostgreSQL and process helpers for research worker pool tests (ADR 0092).

Every integration test gets its own database cloned from one migrated template, and
starts its own ``python -m thytrader.research_worker`` supervisor with an explicit
environment and a scratch working directory (so no ``.env`` is ever read).
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from tests.persistence.test_migration_0048_strategy_root import _alembic

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")
_ROOT = Path(__file__).resolve().parents[2]


def _admin_execute(statement: str) -> None:
    """Run one autocommit statement against the integration server's admin database."""
    if TEST_DATABASE_URL is None:
        raise AssertionError("PostgreSQL integration URL was not configured.")
    admin_url = TEST_DATABASE_URL

    async def run() -> None:
        engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as connection:
                await connection.execute(text(statement))
        finally:
            await engine.dispose()

    asyncio.run(run())


def database_url(name: str) -> str:
    """Return the integration server URL pointed at database ``name``."""
    if TEST_DATABASE_URL is None:
        raise AssertionError("PostgreSQL integration URL was not configured.")
    return make_url(TEST_DATABASE_URL).set(database=name).render_as_string(hide_password=False)


@contextmanager
def migrated_template() -> Iterator[str]:
    """Create one database migrated to head to clone tests from; drop it afterwards."""
    name = f"thytrader_rw_tpl_{uuid4().hex[:10]}"
    _admin_execute(f'CREATE DATABASE "{name}"')
    try:
        upgraded = _alembic(database_url(name), "head")
        if upgraded.returncode != 0:
            raise AssertionError(upgraded.stderr)
        yield name
    finally:
        _admin_execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@contextmanager
def cloned_database(template: str) -> Iterator[str]:
    """Clone the migrated template into a fresh database and yield its URL."""
    name = f"thytrader_rw_{uuid4().hex[:12]}"
    _admin_execute(f'CREATE DATABASE "{name}" TEMPLATE "{template}"')
    try:
        yield database_url(name)
    finally:
        _admin_execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def worker_environment(
    url: str,
    dataset_root: Path,
    *,
    workers: int,
    lease_seconds: int = 3,
    max_jobs: int = 50,
    max_attempts: int = 3,
) -> dict[str, str]:
    """Build the explicit environment one supervisor subprocess runs with."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),  # noqa: S108 - fallback only.
        "THYTRADER_DATABASE_URL": url,
        "THYTRADER_MARKET_DATA_DATASET_ROOT": str(dataset_root),
        "THYTRADER_RESEARCH_WORKER_COUNT": str(workers),
        "THYTRADER_RESEARCH_JOB_LEASE_SECONDS": str(lease_seconds),
        "THYTRADER_RESEARCH_WORKER_MAX_JOBS": str(max_jobs),
        "THYTRADER_RESEARCH_JOB_MAX_ATTEMPTS": str(max_attempts),
        "THYTRADER_LOG_LEVEL": "INFO",
    }


@contextmanager
def research_worker_pool(
    url: str,
    dataset_root: Path,
    workdir: Path,
    *,
    workers: int = 2,
    lease_seconds: int = 3,
    max_jobs: int = 50,
    max_attempts: int = 3,
) -> Iterator[subprocess.Popen[bytes]]:
    """Run ``python -m thytrader.research_worker`` until the block exits (then SIGTERM)."""
    workdir.mkdir(parents=True, exist_ok=True)
    log = (workdir / "research-worker.log").open("wb")
    process = subprocess.Popen(
        [sys.executable, "-m", "thytrader.research_worker"],
        cwd=workdir,
        env=worker_environment(
            url,
            dataset_root,
            workers=workers,
            lease_seconds=lease_seconds,
            max_jobs=max_jobs,
            max_attempts=max_attempts,
        ),
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        log.close()


def worker_log(workdir: Path) -> str:
    """Return the supervisor and worker log (for assertion messages)."""
    path = workdir / "research-worker.log"
    return path.read_text(errors="replace") if path.exists() else ""


def wait_until[T](
    probe: Callable[[], T | None],
    *,
    timeout: float,
    interval: float = 0.1,
    message: str = "condition",
) -> T:
    """Poll ``probe`` until it returns a value other than None (or fail after timeout)."""
    deadline = time.monotonic() + timeout
    while True:
        value = probe()
        if value is not None:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(f"Timed out after {timeout}s waiting for {message}.")
        time.sleep(interval)


def scalar(url: str, statement: str, parameters: dict[str, object] | None = None) -> object:
    """Return one scalar from database ``url``."""

    async def read() -> object:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                return (await connection.execute(text(statement), parameters or {})).scalar()
        finally:
            await engine.dispose()

    return asyncio.run(read())


def rows(
    url: str, statement: str, parameters: dict[str, object] | None = None
) -> list[tuple[object, ...]]:
    """Return every row of one query against database ``url``."""

    async def read() -> list[tuple[object, ...]]:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                result = await connection.execute(text(statement), parameters or {})
                return [tuple(row) for row in result.all()]
        finally:
            await engine.dispose()

    return asyncio.run(read())


def execute(url: str, statement: str, parameters: dict[str, object] | None = None) -> int:
    """Run one mutating statement in its own committed transaction; return rows affected."""

    async def write() -> int:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                result = await connection.execute(text(statement), parameters or {})
                return int(result.rowcount or 0)
        finally:
            await engine.dispose()

    return asyncio.run(write())


def root() -> Path:
    """Return the repository root."""
    return _ROOT
