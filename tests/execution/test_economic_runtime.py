"""Runtime fee gating and deterministic economics across Decimal environments."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Context, Decimal, localcontext

import pytest

from tests.execution.test_loop import _running_snapshot
from tests.trading.test_sizing import _product
from thytrader.exchanges.fees import FeeProfile
from thytrader.execution.loop import _size_entry_or_add
from thytrader.strategies.authoring import create_template_strategy
from thytrader.trading.economics import (
    EconomicEntryGuard,
    EconomicPreflightRequest,
    economic_preflight,
)
from thytrader.trading.geometry import EntrySkipReason
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode, PositionSide
from thytrader.trading.sizing import SizedEntry, size_entry_or_skip


@pytest.mark.parametrize("side", [PositionSide.LONG, PositionSide.SHORT])
def test_runtime_guard_uses_both_fees_for_quantized_targets(side: PositionSide) -> None:
    """A tight target is refused with fees and can be sized when its net hurdle clears."""
    original = create_template_strategy()
    strategy = original.model_copy(
        update={
            "entry": original.entry.model_copy(
                update={
                    "economic_guard": EconomicEntryGuard(minimum_net_target_return_fraction="0")
                }
            )
        }
    )
    assert (
        size_entry_or_skip(
            strategy=strategy,
            cash=Decimal("10000"),
            entry_price=Decimal("100"),
            atr=Decimal("0.125"),
            product=_product(),
            fee_rate=Decimal("0.005"),
            side=side,
        )
        is EntrySkipReason.NET_TARGET_BELOW_MINIMUM
    )
    assert isinstance(
        size_entry_or_skip(
            strategy=strategy,
            cash=Decimal("10000"),
            entry_price=Decimal("100"),
            atr=Decimal("1"),
            product=_product(),
            fee_rate=Decimal("0.005"),
            side=side,
        ),
        SizedEntry,
    )


def test_economic_preflight_does_not_inherit_ambient_precision() -> None:
    """Low precision in a caller cannot change the modeled order or its break-even price."""
    request = EconomicPreflightRequest(
        entry_price="123.456789",
        quantity="0.123456789",
        stop_price="120",
        target_price="128",
        maker_fee_rate="0.005",
        taker_fee_rate="0.009",
        fixed_slippage_bps="5",
        spread_bps="10",
    )
    expected = economic_preflight(request)
    with localcontext(Context(prec=5)):
        assert economic_preflight(request) == expected


@pytest.mark.anyio
async def test_live_guard_requires_an_observed_fee_profile() -> None:
    """Opt-in live gating fails closed without a tier and uses the supplied tier otherwise."""
    original = create_template_strategy()
    strategy = original.model_copy(
        update={
            "entry": original.entry.model_copy(
                update={
                    "economic_guard": EconomicEntryGuard(minimum_net_target_return_fraction="0")
                }
            )
        }
    )
    snapshot = await _running_snapshot(InMemoryExecutionStore(), strategy)
    snapshot = replace(
        snapshot,
        deployment=replace(
            snapshot.deployment, mode=DeploymentMode.LIVE, allocated_capital=Decimal("10000")
        ),
    )
    assert (
        _size_entry_or_add(
            snapshot,
            strategy=strategy,
            product=_product(),
            entry_price=Decimal("100"),
            atr=Decimal("1"),
            side=PositionSide.LONG,
            is_pyramid_add=False,
        )
        is EntrySkipReason.ECONOMICS_FEE_UNAVAILABLE
    )
    result = _size_entry_or_add(
        snapshot,
        strategy=strategy,
        product=_product(),
        entry_price=Decimal("100"),
        atr=Decimal("0.125"),
        side=PositionSide.LONG,
        is_pyramid_add=False,
        fee_profile=FeeProfile(
            maker_fee_rate=Decimal("0.005"),
            taker_fee_rate=Decimal("0.009"),
            usd_volume_30d=Decimal("0"),
            fee_tier="test",
            as_of=datetime.now(UTC),
        ),
    )
    assert result is EntrySkipReason.NET_TARGET_BELOW_MINIMUM
