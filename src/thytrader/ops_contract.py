"""Agent/API content identity that package version 0.1.0 cannot express.

Health and `/health/ready` advertise this contract. The operator CLI compares it to
the copy compiled into this module. A missing or unequal payload means the running
API image is older than the CLI, even when both strings say 0.1.0.

Bump `OPS_CONTRACT_ID` whenever paper/live timeframes, backtest engines, the
historical interval cap, or the expected Alembic revision change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.market_data.models import MAX_HISTORICAL_INTERVAL_COUNT

if TYPE_CHECKING:
    from collections.abc import Mapping

OPS_CONTRACT_ID = "thytrader-ops-contract-v2"
EXPECTED_SCHEMA_REVISION = "0016"
BACKTEST_ENGINES: tuple[str, ...] = (
    "thytrader-bar-backtest-v1",
    "thytrader-bar-backtest-v2",
    "thytrader-bar-backtest-v3",
)
PAPER_TIMEFRAMES: tuple[str, ...] = ("1h", "5m")
LIVE_TIMEFRAMES: tuple[str, ...] = ("1h",)
STALE_IMAGE_REBUILD = "Rebuild and restart with `make run`."


def expected_ops_contract() -> dict[str, object]:
    """Return the CLI/API ops contract this checkout implements."""
    return {
        "id": OPS_CONTRACT_ID,
        "max_historical_interval_count": MAX_HISTORICAL_INTERVAL_COUNT,
        "backtest_engines": list(BACKTEST_ENGINES),
        "paper_timeframes": list(PAPER_TIMEFRAMES),
        "live_timeframes": list(LIVE_TIMEFRAMES),
        "expected_schema_revision": EXPECTED_SCHEMA_REVISION,
    }


def _string_tuple(value: object) -> tuple[str, ...]:
    """Narrow a JSON list to strings; anything else is a mismatch."""
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value)


def ops_contract_matches(payload: Mapping[str, object] | None) -> bool:
    """True when a health payload names this checkout's ops contract."""
    if payload is None:
        return False
    expected = expected_ops_contract()
    return (
        payload.get("id") == expected["id"]
        and payload.get("max_historical_interval_count")
        == expected["max_historical_interval_count"]
        and _string_tuple(payload.get("backtest_engines")) == BACKTEST_ENGINES
        and _string_tuple(payload.get("paper_timeframes")) == PAPER_TIMEFRAMES
        and _string_tuple(payload.get("live_timeframes")) == LIVE_TIMEFRAMES
        and payload.get("expected_schema_revision") == EXPECTED_SCHEMA_REVISION
    )
