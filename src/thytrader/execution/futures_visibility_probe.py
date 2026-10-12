"""Read-only preparation of minimum live futures entry probes, without settling funding."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.futures_paper import _rate
from thytrader.risk.futures_entry_probe import LiveFuturesProbe
from thytrader.trading.futures_book import FuturesBookState, funding_hours_held, funding_overdue
from thytrader.trading.futures_sizing import FuturesMarginTerms

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.market_data.futures_observations import FuturesInstrumentObservation
    from thytrader.trading.futures_book import BoundFuturesContract
    from thytrader.trading.models import DeploymentSnapshot


def visibility_probe(
    snapshot: DeploymentSnapshot,
    *,
    binding: BoundFuturesContract | None,
    observation: FuturesInstrumentObservation | None,
    price: Decimal | None,
    now: datetime,
) -> LiveFuturesProbe:
    """Use the same binding, rates and overdue-hour rules as execution, with no writes."""
    long_rate = _rate(None if observation is None else observation.overnight_long_margin_rate)
    short_rate = _rate(None if observation is None else observation.overnight_short_margin_rate)
    margin = None
    if binding is not None and long_rate is not None and short_rate is not None:
        margin = FuturesMarginTerms(
            contract_size=Decimal(binding.contract.contract_size),
            long_rate=long_rate,
            short_rate=short_rate,
            maintenance_fraction=Decimal(1),
            min_buffer_fraction=Decimal("0.5"),
            max_leverage=Decimal(1),
            fee_per_contract=binding.fee_per_contract,
        )
    due = (
        ()
        if binding is None or binding.contract.kind != "perpetual_future"
        else funding_hours_held(snapshot, snapshot.deployment.product_id, through=now)
    )
    return LiveFuturesProbe(
        state=FuturesBookState(
            deployment_id=snapshot.deployment.id,
            product_id=snapshot.deployment.product_id,
            side="long",
            binding=binding,
            margin=margin,
            funding_overdue=due[0] if due and funding_overdue(due[0], now) else None,
        ),
        price=price,
    )
