"""Shared pytest configuration for the ThyTrader test suite.

The PostgreSQL-backed suites skip themselves when `THYTRADER_TEST_DATABASE_URL`
or `THYTRADER_INTEGRATION_DATABASE_URL` is absent so a laptop checkout stays
runnable. A release gate must not accept those skips as evidence, so CI sets
`THYTRADER_REQUIRE_DATABASE_TESTS=1` and this module turns any such skip into a
failure.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

_DATABASE_URL_VARIABLES: tuple[str, ...] = (
    "THYTRADER_TEST_DATABASE_URL",
    "THYTRADER_INTEGRATION_DATABASE_URL",
)
_STRICT_VARIABLE = "THYTRADER_REQUIRE_DATABASE_TESTS"
_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})


def database_tests_are_required() -> bool:
    """True when the caller demands executed PostgreSQL coverage instead of skips."""
    return os.environ.get(_STRICT_VARIABLE, "").strip().lower() in _TRUTHY


def _is_database_skip(reason: str) -> bool:
    """True when a skip reason names one of the PostgreSQL coverage variables."""
    return any(variable in reason for variable in _DATABASE_URL_VARIABLES)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, None, None]:
    """Fail instead of skipping database coverage when strict mode is requested."""
    del call
    outcome = yield
    if not database_tests_are_required():
        return
    report: pytest.TestReport = outcome.get_result()
    if report.skipped and _is_database_skip(str(report.longrepr)):
        report.outcome = "failed"
        report.longrepr = (
            f"{_STRICT_VARIABLE} is set, so {item.nodeid} must run against a migrated "
            "PostgreSQL database instead of skipping. Configure "
            f"{' and '.join(_DATABASE_URL_VARIABLES)}."
        )
