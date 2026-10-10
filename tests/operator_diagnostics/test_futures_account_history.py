"""Operator ``futures-account --history`` (ADR 0127 §10): the supervised-trade time series."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from typing import TYPE_CHECKING, Any

from fastapi.testclient import TestClient
import pytest

from tests.exchanges.test_coinbase_cfm import _transport
from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.exchanges.coinbase_cfm import CoinbaseCfmAccount
from thytrader.exchanges.futures_models import (
    FuturesAccountObservation,
    FuturesAccountStoreUnavailableError,
    FuturesMarginWindow,
    SpotCollateralBalances,
)
from thytrader.operator import cli
from thytrader.operator.cli import _parser, _query
from thytrader.operator.cli_futures_account import HISTORY_ROUTE, history_usage_error
from thytrader.operator.futures_account_history_report import (
    FuturesAccountHistoryReport,
    build_futures_account_history_report,
)
from thytrader.operator.futures_account_report import build_futures_account_report
from thytrader.operator.models import ReportStatus
from thytrader.operator.status import EXIT_USAGE
from thytrader.worker.futures_mirror import observe_futures_account

if TYPE_CHECKING:
    from collections.abc import Mapping

_START = datetime(2026, 10, 12, 19, 55, tzinfo=UTC)
_SPOT = SpotCollateralBalances(
    usdc_available=Decimal("514.23"),
    usdc_hold=Decimal(0),
    usd_available=Decimal("0.01"),
    usd_hold=Decimal(0),
)


class _History:
    """Serve observations in a window like the PostgreSQL store, or fail."""

    def __init__(self, rows: list[FuturesAccountObservation], *, fail: bool = False) -> None:
        """Hold the canned rows."""
        self.rows = rows
        self.fail = fail
        self.limits: list[int] = []

    async def history(
        self, *, since: datetime, until: datetime, limit: int
    ) -> tuple[FuturesAccountObservation, ...]:
        """Rows in ``[since, until)``, oldest first, at most ``limit``."""
        self.limits.append(limit)
        if self.fail:
            raise FuturesAccountStoreUnavailableError("down")
        matching = sorted(
            (row for row in self.rows if since <= row.observed_at < until),
            key=lambda row: row.observed_at,
        )
        return tuple(matching[:limit])


def _cycle(
    minute: int, *, window: str = "MARGIN_WINDOW_TYPE_INTRADAY"
) -> FuturesAccountObservation:
    """One full mirror cycle ``minute`` minutes after ``_START`` with spot balances."""
    observed = asyncio.run(
        observe_futures_account(
            CoinbaseCfmAccount(_transport()), _START + timedelta(minutes=minute)
        )
    )
    margin_window = FuturesMarginWindow(
        margin_window_type=window,
        end_time=None,
        intraday_killswitch_enabled=False,
        enrollment_killswitch_enabled=False,
    )
    return replace(observed, spot_balances=_SPOT, margin_window=margin_window)


def _build(
    store: _History | None,
    *,
    until_minute: int = 5,
    max_rows: int = 2880,
) -> FuturesAccountHistoryReport:
    """Build over ``[_START, _START + until_minute)`` with the clock just after the window."""
    until = _START + timedelta(minutes=until_minute)
    return asyncio.run(
        build_futures_account_history_report(
            store, since=_START, until=until, now=until + timedelta(seconds=5), max_rows=max_rows
        )
    )


def test_every_cycle_carries_the_measurement_fields() -> None:
    """Each row has the balance summary, margin measures, positions, window and spot USDC."""
    report = _build(_History([_cycle(minute) for minute in range(5)]))
    assert report.overall_status is ReportStatus.HEALTHY
    payload = report.payload
    assert payload.row_count == 5
    assert payload.truncated is False
    assert payload.gaps == ()
    row = payload.rows[0]
    assert row.balance is not None
    balance = row.balance
    for name in (
        "futures_buying_power",
        "cbi_usd_balance",
        "cfm_usd_balance",
        "initial_margin",
        "available_margin",
        "liquidation_threshold",
        "liquidation_buffer_amount",
        "unrealized_pnl",
        "funding_pnl",
        "daily_realized_pnl",
    ):
        assert getattr(balance, name) is not None, name
    assert balance.overnight_margin is not None
    assert row.positions is not None
    assert (row.positions[0].side, row.positions[0].number_of_contracts) == ("short", "1")
    assert row.positions[0].avg_entry_price is not None
    assert row.margin_window_type == "MARGIN_WINDOW_TYPE_INTRADAY"
    assert row.spot_collateral is not None
    assert (row.spot_collateral.usdc_available, row.spot_collateral.usd_available) == (
        "514.23",
        "0.01",
    )
    assert payload.orderable is False
    assert "never added to USDC" in payload.collateral_note


def test_margin_window_changes_are_listed() -> None:
    """The 16:00 ET step-up shows as one change at the first overnight row."""
    rows = [_cycle(0), _cycle(1), _cycle(2, window="MARGIN_WINDOW_TYPE_OVERNIGHT")]
    report = _build(_History(rows), until_minute=3)
    (change,) = report.payload.margin_window_changes
    assert change.observed_at == _START + timedelta(minutes=2)
    assert change.previous_window_type == "MARGIN_WINDOW_TYPE_INTRADAY"
    assert change.window_type == "MARGIN_WINDOW_TYPE_OVERNIGHT"


def test_missed_cycles_are_gaps_including_the_window_edges() -> None:
    """More than three missed cycles, at the start, middle or end, is a named gap."""
    rows = [_cycle(5), _cycle(6), _cycle(12)]
    report = _build(_History(rows), until_minute=20)
    assert report.overall_status is ReportStatus.DEGRADED
    assert [component.reason_code for component in report.components] == ["FUTURES_HISTORY_GAPS"]
    assert [(gap.start, gap.end) for gap in report.payload.gaps] == [
        (_START, _START + timedelta(minutes=5)),
        (_START + timedelta(minutes=6), _START + timedelta(minutes=12)),
        (_START + timedelta(minutes=12), _START + timedelta(minutes=20)),
    ]
    assert report.payload.gaps[1].seconds == 360


def test_truncation_keeps_the_oldest_rows_and_says_so() -> None:
    """One row beyond ``max_rows`` is requested; the page is flagged, with no trailing gap."""
    store = _History([_cycle(minute) for minute in range(5)])
    report = _build(store, max_rows=3, until_minute=60)
    assert store.limits == [4]
    assert report.payload.truncated is True
    assert [row.observed_at for row in report.payload.rows] == [
        _START + timedelta(minutes=minute) for minute in range(3)
    ]
    assert report.payload.gaps == ()
    assert [component.reason_code for component in report.components] == [
        "FUTURES_HISTORY_TRUNCATED"
    ]


def test_unknown_spot_and_failed_reads_are_counted_never_zero() -> None:
    """Rows without spot balances stay null; read failures are counted and named."""
    failed = replace(_cycle(1), spot_balances=None, read_failures=("spot_balances:http_503",))
    report = _build(_History([_cycle(0), failed, _cycle(2)]), until_minute=3)
    assert report.payload.rows[1].spot_collateral is None
    assert report.payload.spot_unknown_rows == 1
    assert report.payload.read_failure_rows == 1
    assert {component.reason_code for component in report.components} == {
        "FUTURES_READ_FAILURES",
        "FUTURES_SPOT_BALANCES_UNKNOWN",
    }


def test_empty_window_and_storage_states_are_named() -> None:
    """No rows, no storage and a failing store each have their own code."""
    empty = _build(_History([]))
    assert [component.reason_code for component in empty.components] == ["FUTURES_HISTORY_EMPTY"]
    assert empty.overall_status is ReportStatus.DEGRADED
    assert _build(None).components[0].reason_code == "STORE_DISABLED"
    failed = _build(_History([], fail=True))
    assert failed.overall_status is ReportStatus.FAILED
    assert failed.components[0].reason_code == "STORE_UNAVAILABLE"


def test_latest_report_shows_the_cycle_spot_collateral() -> None:
    """``futures-account`` carries ``spot_collateral``; ``null`` when it was not read."""

    class _Latest:
        def __init__(self, observation: FuturesAccountObservation) -> None:
            self.observation = observation

        async def record(self, observation: FuturesAccountObservation) -> None:
            del observation
            raise AssertionError("read-only")

        async def latest(self) -> FuturesAccountObservation | None:
            return self.observation

    cycle = _cycle(0)
    known = asyncio.run(build_futures_account_report(_Latest(cycle), now=cycle.observed_at))
    assert known.payload.spot_collateral is not None
    assert known.payload.spot_collateral.usdc_available == "514.23"
    unknown = replace(cycle, spot_balances=None)
    report = asyncio.run(build_futures_account_report(_Latest(unknown), now=cycle.observed_at))
    assert report.payload.spot_collateral is None


def test_route_validates_the_window_and_serves_the_cli_model() -> None:
    """``GET .../futures-account/history`` needs an aware ``since`` before ``until``."""
    app = create_app(Settings(_env_file=None))
    route = "/api/v1/operator/futures-account/history"
    with TestClient(app) as client:
        body = client.get(route, params={"since": "2026-10-12T19:55:00Z"}).json()
        missing = client.get(route)
        naive = client.get(route, params={"since": "2026-10-12T19:55:00"})
        backwards = client.get(
            route, params={"since": "2026-10-12T19:55:00Z", "until": "2026-10-12T19:00:00Z"}
        )
    assert body["report_kind"] == "futures_account_history"
    assert body["components"][0]["reason_code"] == "STORE_DISABLED"
    assert FuturesAccountHistoryReport.model_validate_json(json.dumps(body)).payload.rows == ()
    assert (missing.status_code, naive.status_code, backwards.status_code) == (422, 422, 422)


def test_cli_maps_history_onto_its_route_and_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """``futures-account --history`` calls the history route with ISO since/until."""
    calls: list[tuple[str, Mapping[str, Any]]] = []
    report = _build(_History([_cycle(0)]), until_minute=1)

    def fetch(*, base_url: str, command: str, query: Mapping[str, Any]) -> object:
        del base_url
        calls.append((command, dict(query)))
        return report

    monkeypatch.setattr(cli, "fetch_operator_report", fetch)
    monkeypatch.setattr(cli, "require_matching_ops_contract", lambda _base_url: None)
    monkeypatch.setattr(cli, "_reject_stale_report", lambda _fetched: None)
    with pytest.raises(SystemExit) as exited:
        cli.main(
            [
                "futures-account",
                "--history",
                "--since",
                "2026-10-12T19:55:00Z",
                "--until",
                "2026-10-12T21:00:00+00:00",
            ]
        )
    assert exited.value.code == 0
    assert calls == [
        (
            HISTORY_ROUTE,
            {"since": "2026-10-12T19:55:00+00:00", "until": "2026-10-12T21:00:00+00:00"},
        )
    ]
    plain = _parser().parse_args(["futures-account"])
    assert "since" not in _query(plain)


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["futures-account", "--history"], "--history needs --since."),
        (["futures-account", "--since", "2026-10-12T19:55:00Z"], "--since and --until need"),
        (
            [
                "futures-account",
                "--history",
                "--since",
                "2026-10-12T19:55:00Z",
                "--until",
                "2026-10-12T19:00:00Z",
            ],
            "--until must be later than --since.",
        ),
    ],
)
def test_cli_rejects_incomplete_windows(argv: list[str], message: str) -> None:
    """Window mistakes are usage errors before any report is read."""
    arguments = _parser().parse_args(argv)
    error = history_usage_error(arguments)
    assert error is not None
    assert message in error
    with pytest.raises(SystemExit) as exited:
        cli.main(argv)
    assert exited.value.code == EXIT_USAGE


def test_cli_rejects_naive_instants() -> None:
    """An instant without an offset is refused by argparse (its own exit status 2)."""
    with pytest.raises(SystemExit) as exited:
        cli.main(["futures-account", "--history", "--since", "2026-10-12T19:55:00"])
    assert exited.value.code == 2
