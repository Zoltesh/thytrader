"""Agent/API content identity that package version 0.1.0 cannot express.

Health and `/health/ready` advertise this contract. Operator, data, research, and
runtime CLIs compare it to the copy compiled into this module. A missing or unequal
payload means the running API image is older than the CLI, even when both strings
say 0.1.0. Do not default-fill a missing payload. Do not treat matching `0.1.0` as
current.

Bump `OPS_CONTRACT_ID` whenever paper/live timeframes, backtest engines, the
historical interval cap, the expected Alembic revision, the risk-policy
registry contract, live extras (user-order feed / native OCO),
experiential-memory persistence, or discretionary-order identity change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.market_data.models import MAX_HISTORICAL_INTERVAL_COUNT

if TYPE_CHECKING:
    from collections.abc import Mapping

OPS_CONTRACT_ID = "thytrader-ops-contract-v11"
EXPECTED_SCHEMA_REVISION = "0025"
BACKTEST_ENGINES: tuple[str, ...] = (
    "thytrader-bar-backtest-v1",
    "thytrader-bar-backtest-v2",
    "thytrader-bar-backtest-v3",
)
PAPER_TIMEFRAMES: tuple[str, ...] = ("1h", "5m")
LIVE_TIMEFRAMES: tuple[str, ...] = ("1h", "5m")
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


def ops_contract_matches(payload: Mapping[str, object] | None) -> bool:
    """True only when a health payload exactly equals this checkout's ops contract."""
    if payload is None:
        return False
    return dict(payload) == expected_ops_contract()
