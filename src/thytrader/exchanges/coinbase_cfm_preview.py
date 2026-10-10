"""The CFM per-contract fee probe: one ``orders/preview`` POST (ADR 0128/0129, slice P1-3b).

Coinbase bills futures per contract and no read-only endpoint reports that amount. The
preview endpoint prices a hypothetical order without placing it, so a one-contract market
preview returns the commission one contract would pay. That commission is all-in: the
tier's rate on the notional plus a fixed per-contract part (ADR 0133). The probe keeps the
total, the estimated fill price and the itemized fixed part (venue, clearing and regulatory
commissions); splitting off the rate is the fee report's job, which knows the tier.

This adapter can send exactly one request: a POST to ``/api/v3/brokerage/orders/preview``.
Its transport type exposes ``post`` alone and every request passes the allowlist check
before it is sent, so it can never reach ``orders`` (create), ``orders/batch_cancel``,
``orders/edit`` or ``close_position``. The body is fixed: a one-contract
``market_market_ioc`` on a CDE futures id. The response's ``preview_id`` is never used.
Response text never reaches an error message; failures carry a short reason token.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Literal, Protocol

from thytrader.exchanges.rest_transport import CoinbaseHttpStatusError
from thytrader.market_data.instrument_ids import is_futures_product_id

if TYPE_CHECKING:
    from collections.abc import Mapping

PREVIEW_PATH = "/api/v3/brokerage/orders/preview"
CFM_PREVIEW_POST_ALLOWLIST: frozenset[str] = frozenset({PREVIEW_PATH})
PREVIEW_CONTRACTS = Decimal(1)
_FIXED_COMMISSION_KEYS = ("venue_commission", "clearing_commission", "regulatory_commission")
_TAX_COMMISSION_KEYS = ("gst_commission", "withholding_commission")
_ROUNDING_TOLERANCE = Decimal("0.01")


class PreviewOnlyTransport(Protocol):
    """The only transport capability this adapter receives: a signed JSON POST."""

    def post(self, path: str, data: Mapping[str, object] | None = None) -> dict[str, Any]:
        """POST one REST path and return the JSON object body."""
        ...


class FuturesFeePreviewError(RuntimeError):
    """A failed or refused fee preview, carrying only a short reason token."""

    def __init__(self, reason: str) -> None:
        """Keep the token; never the response body."""
        self.reason = reason
        super().__init__(f"futures fee preview failed: {reason}")


@dataclass(frozen=True, slots=True)
class FuturesFeePreview:
    """The commission Coinbase quotes for one contract of one product.

    ``commission_total`` is the all-in USD commission (the CFM settlement currency) for
    ``contracts`` contracts: rate part plus fixed part, as Coinbase rounds it.
    ``price`` is ``est_average_filled_price`` (USD per underlying unit), ``None`` when the
    response omits it. ``fixed_commission`` is the sum of the itemized
    ``commission_detail_total`` venue, clearing and regulatory commissions, ``None`` when the
    itemization is absent, malformed, or does not add up to ``commission_total`` within a
    cent (never above ``commission_total``).
    """

    product_id: str
    side: Literal["BUY", "SELL"]
    contracts: Decimal
    commission_total: Decimal
    price: Decimal | None
    fixed_commission: Decimal | None
    observed_at: datetime


class CoinbaseCfmFeePreview:
    """Price one hypothetical contract through ``orders/preview``; never place an order."""

    def __init__(self, transport: PreviewOnlyTransport) -> None:
        """Bind a signed transport; only its ``post`` to the preview path is ever called."""
        self._transport = transport

    async def preview_fee(
        self, product_id: str, side: Literal["BUY", "SELL"] = "BUY"
    ) -> FuturesFeePreview:
        """Preview a one-contract market order and return its quoted commission."""
        if not is_futures_product_id(product_id):
            raise FuturesFeePreviewError("not_a_futures_product")
        body = {
            "product_id": product_id,
            "side": side,
            "order_configuration": {
                "market_market_ioc": {"base_size": format(PREVIEW_CONTRACTS, "f")}
            },
        }
        payload = await self._post(PREVIEW_PATH, body)
        errors = payload.get("errs")
        if errors:
            raise FuturesFeePreviewError("preview_rejected")
        commission = _decimal(payload.get("commission_total"))
        if commission is None or commission < 0:
            raise FuturesFeePreviewError("malformed")
        price = _decimal(payload.get("est_average_filled_price"))
        fixed = _itemized_fixed_commission(payload.get("commission_detail_total"), commission)
        return FuturesFeePreview(
            product_id=product_id,
            side=side,
            contracts=PREVIEW_CONTRACTS,
            commission_total=commission,
            price=price if price is not None and price > 0 else None,
            fixed_commission=fixed if fixed is not None and fixed <= commission else None,
            observed_at=datetime.now(UTC),
        )

    async def _post(self, path: str, body: Mapping[str, object]) -> dict[str, Any]:
        """Send one allowlisted POST; refuse any other path before it reaches the transport."""
        if path not in CFM_PREVIEW_POST_ALLOWLIST:
            raise FuturesFeePreviewError("path_not_allowlisted")
        try:
            payload = await asyncio.to_thread(self._transport.post, path, body)
        except CoinbaseHttpStatusError as error:
            raise FuturesFeePreviewError(f"http_{error.status_code}") from None
        except OSError, TimeoutError, TypeError, ValueError:
            raise FuturesFeePreviewError("transport") from None
        if not isinstance(payload, dict):
            raise FuturesFeePreviewError("malformed")
        return payload


def _itemized_fixed_commission(detail: object, total: Decimal) -> Decimal | None:
    """Venue + clearing + regulatory commissions when the itemization explains ``total``.

    ``client_commission`` is Coinbase's rate part and the GST/withholding lines are taxes;
    neither is fixed per contract, so neither is summed into the result. The itemization
    counts only when every line parses as a non-negative amount (absent tax lines are zero)
    and all lines add up to ``total`` within the cent it is rounded to; otherwise ``None``.
    """
    if not isinstance(detail, dict):
        return None
    fixed = [_decimal(detail.get(key)) for key in _FIXED_COMMISSION_KEYS]
    client = _decimal(detail.get("client_commission"))
    taxes = [_decimal(detail.get(key) or "0") for key in _TAX_COMMISSION_KEYS]
    lines = [*fixed, client, *taxes]
    known = [line for line in lines if line is not None and line >= 0]
    if len(known) != len(lines):
        return None
    if abs(sum(known, Decimal(0)) - total) > _ROUNDING_TOLERANCE:
        return None
    return sum((line for line in fixed if line is not None), Decimal(0))


def _decimal(raw: object) -> Decimal | None:
    """Parse a decimal string, or an ``{value, currency}`` USD amount."""
    if isinstance(raw, dict):
        currency = raw.get("currency")
        if currency not in (None, "", "USD"):
            raise FuturesFeePreviewError("non_usd_commission")
        raw = raw.get("value")
    if raw is None or isinstance(raw, bool) or raw == "":
        return None
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None
