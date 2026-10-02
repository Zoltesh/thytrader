"""Optional take-profit and save-time geometry warnings (ADR 0090)."""

from __future__ import annotations

import asyncio
from decimal import Decimal
import json
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.strategies.advisories import (
    StrategyWarningCode,
    plausible_atr_fraction,
    strategy_warnings,
)
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import create_strategy_from_definition
from thytrader.strategies.memory_store import InMemoryStrategyStore
from thytrader.strategies.models import (
    NoTakeProfit,
    RewardRiskTakeProfit,
    StrategyDefinition,
    canonical_strategy_bytes,
    reward_risk_multiple,
    strategy_fingerprint,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_REFERENCE = Path("tests/strategies/golden/reference_strategy_v1.json")
_REFERENCE_FINGERPRINT = "sha256:fc71217907b862f63fbe6f2bdfb218071b155d631c6a9c4faf06f86d2a5db954"


def _definition(
    *,
    side: str = "long",
    timeframe: str = "1h",
    stop_multiple: str = "2",
    take_profit: Mapping[str, str] | None = None,
) -> StrategyDefinition:
    """Template strategy with overridden side, clock, stop multiple, and take-profit."""
    payload = create_template_strategy(timeframe=timeframe).model_dump(mode="python")
    payload["entry"]["side"] = side
    payload["exits"]["initial_stop"]["multiple"] = stop_multiple
    payload["exits"]["take_profit"] = dict(take_profit or {"kind": "reward_risk", "multiple": "2"})
    return StrategyDefinition.model_validate(payload)


def test_existing_reward_risk_documents_keep_their_bytes_and_fingerprint() -> None:
    """Adding ``kind: none`` must not change any existing document's identity."""
    reference = StrategyDefinition.model_validate_json(_REFERENCE.read_text(encoding="utf-8"))
    assert isinstance(reference.exits.take_profit, RewardRiskTakeProfit)
    assert strategy_fingerprint(reference) == _REFERENCE_FINGERPRINT
    canonical = json.loads(canonical_strategy_bytes(reference))
    assert canonical["exits"]["take_profit"] == {"kind": "reward_risk", "multiple": "2"}


def test_take_profit_none_validates_and_serializes_canonically() -> None:
    """``{"kind": "none"}`` is the only canonical form and rejects a stray multiple."""
    definition = _definition(take_profit={"kind": "none"})
    assert definition.exits.take_profit == NoTakeProfit(kind="none")
    assert reward_risk_multiple(definition.exits) is None
    canonical = json.loads(canonical_strategy_bytes(definition))
    assert canonical["exits"]["take_profit"] == {"kind": "none"}
    with pytest.raises(ValidationError):
        _definition(take_profit={"kind": "none", "multiple": "2"})
    with pytest.raises(ValidationError):
        _definition(take_profit={"kind": "trailing"})
    assert reward_risk_multiple(_definition().exits) == Decimal("2")


def test_plausible_atr_ceiling_scales_with_the_square_root_of_time() -> None:
    """Daily bars allow 20% ATR; hourly bars about 4%."""
    assert plausible_atr_fraction("1d") == Decimal("0.2000")
    assert plausible_atr_fraction("1h") == Decimal("0.0408")
    assert plausible_atr_fraction("4h") == Decimal("0.0816")


def test_short_take_profit_that_can_go_non_positive_warns_at_save_time() -> None:
    """The AVAX repro (1d short, 3 ATR stop, 10R target) warns before any backtest."""
    warnings = strategy_warnings(
        _definition(
            side="short",
            timeframe="1d",
            stop_multiple="3",
            take_profit={"kind": "reward_risk", "multiple": "10"},
        )
    )
    assert [item.code for item in warnings] == [
        StrategyWarningCode.SHORT_TARGET_MAY_BE_NON_POSITIVE
    ]
    assert warnings[0].loc == ("exits", "take_profit", "multiple")
    assert "3.33%" in warnings[0].message
    assert "target_not_positive" in warnings[0].message


@pytest.mark.parametrize(
    ("side", "timeframe", "stop_multiple", "take_profit"),
    [
        ("short", "1d", "2", {"kind": "reward_risk", "multiple": "2"}),
        ("short", "1d", "10", {"kind": "none"}),
        ("short", "1h", "3", {"kind": "reward_risk", "multiple": "3"}),
        ("long", "1h", "2", {"kind": "reward_risk", "multiple": "10"}),
    ],
)
def test_geometry_that_stays_positive_for_plausible_volatility_does_not_warn(
    side: str, timeframe: str, stop_multiple: str, take_profit: Mapping[str, str]
) -> None:
    """No warning when the non-positive threshold exceeds the plausible ATR ceiling."""
    definition = _definition(
        side=side, timeframe=timeframe, stop_multiple=stop_multiple, take_profit=take_profit
    )
    assert strategy_warnings(definition) == ()


def test_long_stop_that_can_go_non_positive_warns() -> None:
    """A 10 ATR long stop on daily bars is non-positive at 10% ATR, which is plausible."""
    warnings = strategy_warnings(_definition(timeframe="1d", stop_multiple="10"))
    assert [item.code for item in warnings] == [StrategyWarningCode.LONG_STOP_MAY_BE_NON_POSITIVE]


def test_strategy_api_reports_warnings_without_blocking_the_save() -> None:
    """The warning is advisory: the strategy stays valid and startable."""
    store = InMemoryStrategyStore()
    risky = _definition(
        side="short",
        timeframe="1d",
        stop_multiple="3",
        take_profit={"kind": "reward_risk", "multiple": "10"},
    )
    record = asyncio.run(create_strategy_from_definition(store, risky))
    app = create_app(Settings(_env_file=None), strategy_store=store)
    with TestClient(app) as client:
        body = client.get(f"/api/v1/strategies/{record.strategy_id}").json()
    validation = body["validation"]
    assert validation["valid"] is True
    assert validation["issues"] == []
    assert [item["code"] for item in validation["warnings"]] == ["short_target_may_be_non_positive"]
    assert validation["warnings"][0]["loc"] == "exits.take_profit.multiple"
