"""Futures fee evidence for the operator ``fees`` report (ADR 0128, slice P1-3).

A read-only GET of ``transaction_summary?product_type=FUTURE``. It never fails the spot fee
report: an adapter without the read (demo) is ``unsupported`` and a failed read is
``read_failed``, both ``unavailable`` with no numbers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from thytrader.exchanges.read_errors import ExchangeReadError, ExchangeReadFailureKind
from thytrader.operator.portfolio_models import FuturesFeesPayload

if TYPE_CHECKING:
    from thytrader.exchanges.fees import FeeProfile


class FuturesFeeReader(Protocol):
    """The one portfolio read this report needs."""

    async def get_futures_fee_profile(self) -> FeeProfile:
        """Return the futures fee tier or raise a typed read error."""
        ...


async def futures_fee_evidence(portfolio: FuturesFeeReader) -> FuturesFeesPayload:
    """Return the futures fee tier as Coinbase reports it, or why it is unavailable."""
    try:
        profile = await portfolio.get_futures_fee_profile()
    except ExchangeReadError as error:
        unsupported = error.failure.kind is ExchangeReadFailureKind.UNSUPPORTED
        return FuturesFeesPayload(
            status="unavailable",
            unavailable_reason="unsupported" if unsupported else "read_failed",
        )
    except Exception:  # noqa: BLE001 - provider failures are redacted at this boundary.
        return FuturesFeesPayload(status="unavailable", unavailable_reason="read_failed")
    return FuturesFeesPayload(
        status="available",
        maker_fee_rate=format(profile.maker_fee_rate, "f"),
        taker_fee_rate=format(profile.taker_fee_rate, "f"),
        usd_volume_30d=format(profile.usd_volume_30d, "f"),
        fee_tier=profile.fee_tier,
        as_of=profile.as_of,
    )
