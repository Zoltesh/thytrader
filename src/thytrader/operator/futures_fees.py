"""Futures fee evidence for the operator ``fees`` report (ADR 0128, slices P1-3/P1-3b).

A read-only GET of ``transaction_summary?product_type=FUTURE``. It never fails the spot fee
report: an adapter without the read (demo) is ``unsupported`` and a failed read is
``read_failed``, both ``unavailable`` with no numbers.

Only when the caller names ``preview_product_id`` does the report also send the single
allowlisted ``orders/preview`` POST for one contract (places no order) and report the quoted
commission as ``fee_per_contract`` (``orders_preview``). A failed preview leaves it null.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from thytrader.decimal_text import canonical_decimal
from thytrader.exchanges.coinbase_cfm_preview import FuturesFeePreviewError
from thytrader.exchanges.read_errors import ExchangeReadError, ExchangeReadFailureKind
from thytrader.operator.portfolio_models import FuturesFeesPayload

if TYPE_CHECKING:
    from thytrader.exchanges.coinbase_cfm_preview import FuturesFeePreview
    from thytrader.exchanges.fees import FeeProfile


class FuturesFeeReader(Protocol):
    """The one portfolio read this report needs."""

    async def get_futures_fee_profile(self) -> FeeProfile:
        """Return the futures fee tier or raise a typed read error."""
        ...

    async def preview_futures_fee(self, product_id: str) -> FuturesFeePreview:
        """Quote one contract's commission or raise a typed error."""
        ...


async def futures_fee_evidence(
    portfolio: FuturesFeeReader, preview_product_id: str | None = None
) -> FuturesFeesPayload:
    """Return the futures fee tier, plus a one-contract preview when one is requested."""
    tier = await _fee_tier(portfolio)
    if preview_product_id is None:
        return tier
    try:
        preview = await portfolio.preview_futures_fee(preview_product_id)
    except FuturesFeePreviewError as error:
        reason = error.reason
    except ExchangeReadError as error:
        unsupported = error.failure.kind is ExchangeReadFailureKind.UNSUPPORTED
        reason = "unsupported" if unsupported else "read_failed"
    except Exception:  # noqa: BLE001 - provider failures are redacted at this boundary.
        reason = "read_failed"
    else:
        return tier.model_copy(
            update={
                "fee_per_contract": canonical_decimal(preview.fee_per_contract),
                "fee_per_contract_source": "orders_preview",
                "preview_product_id": preview.product_id,
                "preview_commission_total": canonical_decimal(preview.commission_total),
                "preview_observed_at": preview.observed_at,
            }
        )
    return tier.model_copy(
        update={"preview_product_id": preview_product_id, "preview_unavailable_reason": reason}
    )


async def _fee_tier(portfolio: FuturesFeeReader) -> FuturesFeesPayload:
    """The futures fee tier as Coinbase reports it, or why it is unavailable."""
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
