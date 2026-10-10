"""Futures fee evidence for the operator ``fees`` report (ADR 0128, slices P1-3/P1-3b).

A read-only GET of ``transaction_summary?product_type=FUTURE``. It never fails the spot fee
report: an adapter without the read (demo) is ``unsupported`` and a failed read is
``read_failed``, both ``unavailable`` with no numbers.

Only when the caller names ``preview_product_id`` does the report also send the single
allowlisted ``orders/preview`` POST for one contract (places no order). The quoted commission
is all-in (taker rate on the notional plus a fixed per-contract part), while paper books and
backtests charge ``notional x rate + fee_per_contract``; so ``fee_per_contract`` is the fixed
part alone (ADR 0133). It is the preview's itemized fixed commissions when present, else
``commission_total - taker_rate x contract_size x price``. A failed preview, or a fixed part
that cannot be derived, leaves it null with a reason; it is never guessed.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from thytrader.decimal_text import canonical_decimal
from thytrader.exchanges.coinbase_cfm_preview import FuturesFeePreviewError
from thytrader.exchanges.read_errors import ExchangeReadError, ExchangeReadFailureKind
from thytrader.operator.portfolio_models import (
    FeePerContractSource,
    FeePerContractUnavailableReason,
    FuturesFeesPayload,
)

if TYPE_CHECKING:
    from thytrader.exchanges.coinbase_cfm_preview import FuturesFeePreview
    from thytrader.exchanges.fees import FeeProfile
    from thytrader.market_data.instruments import Instrument


class FuturesFeeReader(Protocol):
    """The one portfolio read this report needs."""

    async def get_futures_fee_profile(self) -> FeeProfile:
        """Return the futures fee tier or raise a typed read error."""
        ...

    async def preview_futures_fee(self, product_id: str) -> FuturesFeePreview:
        """Quote one contract's commission or raise a typed error."""
        ...


class FuturesContractCatalog(Protocol):
    """The catalog read that supplies a contract's size for the rate split."""

    async def enabled_instrument(self, product_id: str) -> Instrument | None:
        """Return the enabled instrument, or ``None`` when absent or disabled."""
        ...


async def futures_fee_evidence(
    portfolio: FuturesFeeReader,
    preview_product_id: str | None = None,
    catalog: FuturesContractCatalog | None = None,
) -> FuturesFeesPayload:
    """Return the futures fee tier, plus a one-contract preview when one is requested.

    ``catalog`` supplies the contract size used when the preview has no itemized fixed
    commissions; without it that fallback reports ``contract_size_unavailable``.
    """
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
        contract_size = await _contract_size(catalog, preview.product_id)
        return split_preview_commission(tier, preview, contract_size)
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


def split_preview_commission(
    tier: FuturesFeesPayload, preview: FuturesFeePreview, contract_size: Decimal | None
) -> FuturesFeesPayload:
    """Report the preview with its all-in commission split into rate and fixed parts.

    The rate part is ``taker_fee_rate x contract_size x price`` (a market preview is a
    taker fill). ``fee_per_contract`` is the itemized fixed commission when Coinbase
    itemizes it, else the all-in total minus the rate part; a negative difference is
    malformed and stays null.
    """
    taker = None if tier.taker_fee_rate is None else Decimal(tier.taker_fee_rate)
    price = preview.price
    rate_part = None
    if taker is not None and price is not None and contract_size is not None:
        rate_part = taker * contract_size * price * preview.contracts
    fee: Decimal | None = None
    source: FeePerContractSource = "operator_input"
    reason: FeePerContractUnavailableReason | None = None
    if preview.fixed_commission is not None:
        fee, source = preview.fixed_commission / preview.contracts, "orders_preview_itemized"
    elif rate_part is None:
        reason = _rate_part_gap(taker, price)
    elif rate_part > preview.commission_total:
        reason = "negative_fixed_fee"
    else:
        fee = (preview.commission_total - rate_part) / preview.contracts
        source = "orders_preview_less_taker_rate"
    return tier.model_copy(
        update={
            "fee_per_contract": None if fee is None else canonical_decimal(fee),
            "fee_per_contract_source": source,
            "fee_per_contract_unavailable_reason": reason,
            "preview_product_id": preview.product_id,
            "preview_commission_total": canonical_decimal(preview.commission_total),
            "preview_price": _text(price),
            "preview_contract_size": _text(contract_size),
            "preview_rate_commission": _text(rate_part),
            "preview_fixed_commission_itemized": _text(preview.fixed_commission),
            "preview_observed_at": preview.observed_at,
        }
    )


def _rate_part_gap(taker: Decimal | None, price: Decimal | None) -> FeePerContractUnavailableReason:
    """Name the first input the rate part lacks (the contract size when rate and price exist)."""
    if taker is None:
        return "taker_fee_rate_unavailable"
    if price is None:
        return "preview_price_unavailable"
    return "contract_size_unavailable"


async def _contract_size(catalog: FuturesContractCatalog | None, product_id: str) -> Decimal | None:
    """The catalog's contract size for ``product_id``, or ``None`` when it cannot be read."""
    if catalog is None:
        return None
    try:
        instrument = await catalog.enabled_instrument(product_id)
    except Exception:  # noqa: BLE001 - a catalog failure only removes the fallback split.
        return None
    future = None if instrument is None else instrument.future
    if future is None or future.contract_size <= 0:
        return None
    return future.contract_size


def _text(value: Decimal | None) -> str | None:
    """Canonical decimal text, or ``None``."""
    return None if value is None else canonical_decimal(value)
