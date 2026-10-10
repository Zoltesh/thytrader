"""Operator ``futures-account`` report (ADR 0127): truthful states, USD only."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from tests.exchanges.test_coinbase_cfm import _transport
from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.exchanges.coinbase_cfm import CoinbaseCfmAccount
from thytrader.exchanges.futures_models import (
    FuturesAccountObservation,
    FuturesAccountStoreUnavailableError,
)
from thytrader.exchanges.rest_transport import CoinbaseHttpStatusError
from thytrader.operator.futures_account_report import (
    FuturesAccountReport,
    build_futures_account_report,
)
from thytrader.operator.models import ReportStatus
from thytrader.worker.futures_mirror import observe_futures_account

_NOW = datetime(2026, 10, 10, 2, tzinfo=UTC)


class _Store:
    """Serve one stored observation, nothing, or a storage failure."""

    def __init__(self, latest: FuturesAccountObservation | None, *, fail: bool = False) -> None:
        """Hold the canned answer."""
        self._latest = latest
        self.fail = fail

    async def record(self, observation: FuturesAccountObservation) -> None:
        """Unused by the report."""
        del observation
        raise AssertionError("the report is read-only")

    async def latest(self) -> FuturesAccountObservation | None:
        """Return the canned observation."""
        if self.fail:
            raise FuturesAccountStoreUnavailableError("down")
        return self._latest


def _observation(**overrides: Any) -> FuturesAccountObservation:
    """A full observation from the documented fixtures, observed at ``_NOW``."""
    transport = _transport(**overrides)
    return asyncio.run(observe_futures_account(CoinbaseCfmAccount(transport), _NOW))


def _build(store: _Store | None, now: datetime = _NOW) -> FuturesAccountReport:
    """Build the report at a fixed clock."""
    return asyncio.run(build_futures_account_report(store, now=now))


def test_current_full_snapshot_is_healthy_and_in_usd() -> None:
    """Amounts are USD strings; positions are in contracts; nothing is orderable."""
    report = _build(_Store(_observation()), now=_NOW + timedelta(seconds=30))
    assert report.overall_status is ReportStatus.HEALTHY
    payload = report.payload
    assert payload.enablement == "enabled"
    assert payload.balance is not None
    assert payload.balance.currency == "USD"
    assert (payload.balance.cbi_usd_balance, payload.balance.cfm_usd_balance) == (
        "425.00",
        "75.00",
    )
    assert payload.positions is not None
    assert payload.positions[0].number_of_contracts == "1"
    assert payload.positions[0].side == "short"
    assert payload.orderable is False
    assert payload.age_seconds == 30


def test_failed_reads_stale_snapshots_and_missing_storage_are_named() -> None:
    """Unknown enablement, staleness, no snapshot and storage failure each have a code."""

    def fail() -> dict[str, Any]:
        raise CoinbaseHttpStatusError(401, None)

    unknown = _build(_Store(_observation(balance=fail)), now=_NOW + timedelta(minutes=5))
    assert {c.reason_code for c in unknown.components} == {
        "FUTURES_MIRROR_STALE",
        "FUTURES_ACCOUNT_UNKNOWN",
    }
    assert unknown.payload.balance is None
    assert unknown.payload.read_failures == ("balance_summary:http_401",)
    partial = _build(_Store(_observation(positions=fail)))
    assert partial.components[0].reason_code == "FUTURES_READ_FAILURES"
    assert partial.payload.positions is None
    assert _build(_Store(None)).components[0].reason_code == "FUTURES_MIRROR_NOT_RUN"
    failed = _build(_Store(None, fail=True))
    assert failed.overall_status is ReportStatus.FAILED
    assert _build(None).components[0].reason_code == "STORE_DISABLED"


def test_no_positions_is_an_empty_list_not_unknown() -> None:
    """The venue reporting no positions is ``[]``; a failed read is ``null``."""
    observation = replace(_observation(), positions=())
    assert _build(_Store(observation)).payload.positions == ()


def test_route_without_storage_and_report_round_trip() -> None:
    """The HTTP route serves the same model the CLI validates."""
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        body = client.get("/api/v1/operator/futures-account").json()
    assert body["report_kind"] == "futures_account"
    assert body["components"][0]["reason_code"] == "STORE_DISABLED"
    report = _build(_Store(_observation()))
    assert FuturesAccountReport.model_validate_json(report.model_dump_json()) == report
