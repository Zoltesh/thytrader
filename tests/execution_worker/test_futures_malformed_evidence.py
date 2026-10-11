"""Malformed raw venue evidence remains unknown through the staged source boundary."""

from __future__ import annotations

from decimal import localcontext

import pytest

from tests.exchanges.test_coinbase_cfm import _transport
from tests.execution.test_paper_futures_books import _PERP, _Observations
from tests.execution_worker.test_futures_source import NoExternalOrders
from thytrader.exchanges.coinbase_cfm import CoinbaseCfmAccount
from thytrader.execution.futures_sources import RecordedFuturesSource


@pytest.mark.anyio
@pytest.mark.parametrize("traps", [False, True])
@pytest.mark.parametrize(
    "invalid",
    [
        {"number_of_contracts": "NaN"},
        {"number_of_contracts": "sNaN"},
        {"number_of_contracts": "Infinity"},
        {"number_of_contracts": "1.1"},
        {"number_of_contracts": "-1"},
        {"number_of_contracts": 1.0},
        {"side": "UNKNOWN"},
        {"current_price": "sNaN"},
        {"expiration_time": "0001-01-01T00:00:00+14:00"},
    ],
)
async def test_malformed_positions_never_certify_flat_or_whole_contracts(
    invalid: dict[str, object],
    traps: bool,
) -> None:
    """One malformed member invalidates the complete account read, even with Decimal traps off."""
    row = {"product_id": _PERP, "side": "LONG", "number_of_contracts": "1", **invalid}
    source = RecordedFuturesSource(
        CoinbaseCfmAccount(_transport(positions=lambda: {"positions": [row]})),
        NoExternalOrders(),
        _Observations(()),
        mirror=None,
    )
    with localcontext() as context:
        for signal in context.traps:
            context.traps[signal] = traps
        account = await source.read_account(frozenset(), frozenset({_PERP}))
    assert account.venue.positions is None
    assert not account.marks


@pytest.mark.anyio
async def test_duplicate_position_members_do_not_overwrite_signed_inventory() -> None:
    """Last-row-wins parsing cannot hide unknown or conflicting venue inventory."""
    row = {"product_id": _PERP, "side": "LONG", "number_of_contracts": "1"}
    source = RecordedFuturesSource(
        CoinbaseCfmAccount(_transport(positions=lambda: {"positions": [row, row]})),
        NoExternalOrders(),
        _Observations(()),
        mirror=None,
    )
    account = await source.read_account(frozenset(), frozenset({_PERP}))
    assert account.venue.positions is None
