"""Coinbase CFM futures listing adapter: a separate, strict parser for FCM product rows.

ADR 0126. The spot parser in ``coinbase_market_data`` is deliberately not loosened:
futures rows send ``base_currency_id: ""`` and ``quote_min_size: "0"``, which the spot
parser rejects, and that keeps futures out of every spot surface.

The listing is read from the public ``GET /market/products?product_type=FUTURE``
endpoint with explicit ``limit``/``offset`` paging. Verified live on 2026-10-10:
``num_products`` echoes the page's row count (it is not a total), ``has_next`` is honest
under paging, and ``expiring_contract_status=STATUS_ALL`` returns 242 rows, so the
unexpired 100-row answer is not a silent cap. Completeness is still proved per page:
a page larger than requested, a full page with no ``has_next``, a repeated id, or a
listing that never ends fails closed.

INTX rows (``product_venue: INTX``) are retired international perps and are excluded.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import re
from typing import TYPE_CHECKING, Any, Protocol

from thytrader.market_data.instrument_ids import (
    FUTURES_SETTLEMENT_CURRENCY,
    futures_contract_code,
    futures_listed_expiry,
    is_futures_product_id,
)
from thytrader.market_data.instruments import (
    FundingObservation,
    FuturesProduct,
    FuturesSession,
    InstrumentKind,
    MaintenanceWindow,
    MarginRates,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from thytrader.exchanges.coinbase_market_data import CoinbaseResponse

FUTURES_LISTING_PAGE_SIZE = 50
_MAX_PAGES = 20
_FCM_VENUE = "FCM"
_FUNDING_INTERVAL = re.compile(r"^([1-9][0-9]{0,6})s$")


class CoinbaseFuturesCatalogError(ValueError):
    """Signal a malformed or possibly incomplete futures listing; never a partial result."""


class CoinbaseFuturesListingClient(Protocol):
    """The public product-listing call of the official Coinbase SDK."""

    def get_public_products(
        self,
        limit: int | None = None,
        offset: int | None = None,
        product_type: str | None = None,
        product_ids: list[str] | None = None,
        contract_expiry_type: str | None = None,
        expiring_contract_status: str | None = None,
        get_all_products: bool = False,
    ) -> CoinbaseResponse:
        """Return one page of the public product listing."""
        ...


class CoinbaseFuturesCatalog:
    """Read the complete Coinbase CFM futures listing as validated domain products."""

    def __init__(self, client: CoinbaseFuturesListingClient) -> None:
        """Wrap an official SDK client; only the public listing call is used."""
        self._client = client

    async def list_futures_products(self) -> tuple[FuturesProduct, ...]:
        """Page through the listing and return every FCM contract, sorted by id."""
        products: dict[str, FuturesProduct] = {}
        for page_index in range(_MAX_PAGES):
            payload = await self._page(page_index * FUTURES_LISTING_PAGE_SIZE)
            raw_rows = _page_rows(payload)
            for row in raw_rows:
                product = parse_futures_row(row)
                if product is None:
                    continue
                if product.product_id in products:
                    message = f"Coinbase futures listing repeated {product.product_id}."
                    raise CoinbaseFuturesCatalogError(message)
                products[product.product_id] = product
            if not _has_next(payload, len(raw_rows)):
                break
        else:
            message = "Coinbase futures listing did not end within the page budget."
            raise CoinbaseFuturesCatalogError(message)
        if not products:
            message = "Coinbase returned an empty futures listing."
            raise CoinbaseFuturesCatalogError(message)
        return tuple(products[key] for key in sorted(products))

    async def _page(self, offset: int) -> dict[str, Any]:
        """Fetch one listing page; transport detail is dropped because it can echo URLs."""
        try:
            response = await asyncio.to_thread(
                self._client.get_public_products,
                limit=FUTURES_LISTING_PAGE_SIZE,
                offset=offset,
                product_type="FUTURE",
            )
        except OSError, TypeError, ValueError:
            # SDK HTTP and transport errors are OSError (requests); the detail can echo URLs.
            message = "Coinbase futures listing could not be read."
            raise CoinbaseFuturesCatalogError(message) from None
        return response.to_dict()


def _page_rows(payload: Mapping[str, object]) -> list[dict[str, object]]:
    """Return one page's product objects or fail closed on any malformed shape."""
    raw = payload.get("products")
    if not isinstance(raw, list):
        message = "Coinbase futures listing did not include a product list."
        raise CoinbaseFuturesCatalogError(message)
    if len(raw) > FUTURES_LISTING_PAGE_SIZE:
        message = "Coinbase futures listing ignored the page size; coverage is unprovable."
        raise CoinbaseFuturesCatalogError(message)
    return [_text_keys(row, "product") for row in raw]


def _has_next(payload: Mapping[str, object], row_count: int) -> bool:
    """Return whether another page follows; a full page without ``has_next`` fails closed."""
    pagination = payload.get("pagination")
    has_next = pagination.get("has_next") if isinstance(pagination, dict) else None
    if has_next is True:
        if row_count == 0:
            message = "Coinbase futures listing reported more rows after an empty page."
            raise CoinbaseFuturesCatalogError(message)
        return True
    if has_next is False:
        return False
    if row_count >= FUTURES_LISTING_PAGE_SIZE:
        message = "Coinbase futures listing page was full with no has_next; coverage unprovable."
        raise CoinbaseFuturesCatalogError(message)
    return False


def parse_futures_row(row: Mapping[str, object]) -> FuturesProduct | None:
    """Validate one listing row; ``None`` for a non-FCM (for example INTX) row."""
    if row.get("product_venue") != _FCM_VENUE:
        return None
    if row.get("product_type") != "FUTURE":
        message = "Coinbase FCM listing row was not a FUTURE product."
        raise CoinbaseFuturesCatalogError(message)
    product_id = _required_text(row, "product_id")
    if not is_futures_product_id(product_id) or product_id != product_id.strip().upper():
        message = f"Coinbase FCM product id {product_id!r} is not a CDE futures id."
        raise CoinbaseFuturesCatalogError(message)
    if _required_text(row, "quote_currency_id") != FUTURES_SETTLEMENT_CURRENCY:
        message = f"Coinbase FCM product {product_id} does not settle in USD."
        raise CoinbaseFuturesCatalogError(message)
    details = _text_keys(row.get("future_product_details"), "future_product_details")
    funding = _funding(details)
    venue_expiry = _required_instant(details, "contract_expiry")
    return FuturesProduct(
        product_id=product_id,
        kind=InstrumentKind.DATED_FUTURE if funding is None else InstrumentKind.PERPETUAL_FUTURE,
        contract_code=futures_contract_code(product_id),
        underlying=_required_text(details, "contract_root_unit"),
        settlement_currency=FUTURES_SETTLEMENT_CURRENCY,
        contract_size=_positive_decimal(details, "contract_size"),
        price_increment=_positive_decimal(row, "price_increment"),
        base_increment=_positive_decimal(row, "base_increment"),
        base_min_size=_positive_decimal(row, "base_min_size"),
        venue_expiry_at=venue_expiry,
        listed_expiry=_listed_expiry(product_id),
        twenty_four_by_seven=_required_bool(details, "twenty_four_by_seven"),
        intraday_margin=_margin_rates(details, "intraday_margin_rate"),
        overnight_margin=_margin_rates(details, "overnight_margin_rate"),
        funding=funding,
        session=_session(row.get("fcm_trading_session_details")),
        asset_type=_optional_text(details, "futures_asset_type"),
        display_name=_optional_text(row, "display_name"),
        trading_enabled=(
            row.get("is_disabled") is not True and row.get("trading_disabled") is not True
        ),
    )


def _listed_expiry(product_id: str) -> date:
    """Return the id-encoded expiry day, mapping an impossible date to a parse error."""
    try:
        return futures_listed_expiry(product_id)
    except ValueError as error:
        raise CoinbaseFuturesCatalogError(str(error)) from None


def _funding(details: Mapping[str, object]) -> FundingObservation | None:
    """Return perp funding facts, or ``None`` for a dated contract (no funding interval)."""
    raw_interval = details.get("funding_interval")
    if raw_interval is None or raw_interval == "":
        return None
    if not isinstance(raw_interval, str):
        message = "Coinbase futures funding_interval was not text."
        raise CoinbaseFuturesCatalogError(message)
    match = _FUNDING_INTERVAL.fullmatch(raw_interval)
    if match is None:
        message = f"Coinbase futures funding_interval {raw_interval!r} is not whole seconds."
        raise CoinbaseFuturesCatalogError(message)
    return FundingObservation(
        interval=timedelta(seconds=int(match.group(1))),
        rate=_optional_decimal(details, "funding_rate"),
        funding_time=_optional_instant(details, "funding_time"),
    )


def _margin_rates(details: Mapping[str, object], field: str) -> MarginRates | None:
    """Return long/short margin fractions, ``None`` when unlisted; malformed fails closed."""
    raw = details.get(field)
    if raw is None:
        return None
    rates = _text_keys(raw, field)
    long_rate = _optional_decimal(rates, "long_margin_rate")
    short_rate = _optional_decimal(rates, "short_margin_rate")
    if long_rate is None or short_rate is None:
        return None
    if long_rate <= 0 or short_rate <= 0:
        message = f"Coinbase futures {field} must be positive."
        raise CoinbaseFuturesCatalogError(message)
    return MarginRates(long=long_rate, short=short_rate)


def _session(raw: object) -> FuturesSession:
    """Return session facts; an absent block is all-unknown rather than closed or open."""
    if raw is None:
        return FuturesSession(None, None, None, None, None)
    session = _text_keys(raw, "fcm_trading_session_details")
    is_open = session.get("is_session_open")
    maintenance: MaintenanceWindow | None = None
    raw_maintenance = session.get("maintenance")
    if raw_maintenance is not None:
        window = _text_keys(raw_maintenance, "maintenance")
        starts_at = _required_instant(window, "start_time")
        ends_at = _required_instant(window, "end_time")
        if ends_at <= starts_at:
            message = "Coinbase futures maintenance window ends before it starts."
            raise CoinbaseFuturesCatalogError(message)
        maintenance = MaintenanceWindow(starts_at=starts_at, ends_at=ends_at)
    return FuturesSession(
        is_open=is_open if isinstance(is_open, bool) else None,
        state=_optional_text(session, "session_state"),
        opens_at=_optional_instant(session, "open_time"),
        closes_at=_optional_instant(session, "close_time"),
        maintenance=maintenance,
    )


def _text_keys(raw: object, label: str) -> dict[str, object]:
    """Narrow one untrusted JSON object to text keys, or fail closed."""
    if not isinstance(raw, dict):
        message = f"Coinbase futures {label} was not an object."
        raise CoinbaseFuturesCatalogError(message)
    narrowed: dict[str, object] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            message = f"Coinbase futures {label} had a non-text field name."
            raise CoinbaseFuturesCatalogError(message)
        narrowed[key] = value
    return narrowed


def _required_text(payload: Mapping[str, object], field: str) -> str:
    """Return one required non-empty text field."""
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        message = f"Coinbase futures field {field!r} must be non-empty text."
        raise CoinbaseFuturesCatalogError(message)
    return value.strip()


def _optional_text(payload: Mapping[str, object], field: str) -> str | None:
    """Return optional text; the venue sends ``""`` for "none"."""
    value = payload.get(field)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        message = f"Coinbase futures field {field!r} was not text."
        raise CoinbaseFuturesCatalogError(message)
    return value.strip() or None


def _required_bool(payload: Mapping[str, object], field: str) -> bool:
    """Return one required JSON boolean; text or numbers fail closed."""
    value = payload.get(field)
    if not isinstance(value, bool):
        message = f"Coinbase futures field {field!r} must be a boolean."
        raise CoinbaseFuturesCatalogError(message)
    return value


def _optional_decimal(payload: Mapping[str, object], field: str) -> Decimal | None:
    """Return a finite decimal string, ``None`` for absent or ``""``."""
    raw = payload.get(field)
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        message = f"Coinbase futures field {field!r} must be a decimal string."
        raise CoinbaseFuturesCatalogError(message)
    try:
        value = Decimal(raw)
    except InvalidOperation:
        message = f"Coinbase futures field {field!r} is not a valid decimal."
        raise CoinbaseFuturesCatalogError(message) from None
    if not value.is_finite():
        message = f"Coinbase futures field {field!r} must be finite."
        raise CoinbaseFuturesCatalogError(message)
    return value


def _positive_decimal(payload: Mapping[str, object], field: str) -> Decimal:
    """Return one required positive decimal-string field."""
    value = _optional_decimal(payload, field)
    if value is None or value <= 0:
        message = f"Coinbase futures field {field!r} must be a positive decimal."
        raise CoinbaseFuturesCatalogError(message)
    return value


def _optional_instant(payload: Mapping[str, object], field: str) -> datetime | None:
    """Return an RFC 3339 UTC instant, ``None`` when absent or ``""``."""
    raw = payload.get(field)
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        message = f"Coinbase futures field {field!r} must be an RFC 3339 string."
        raise CoinbaseFuturesCatalogError(message)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        message = f"Coinbase futures field {field!r} is not an RFC 3339 instant."
        raise CoinbaseFuturesCatalogError(message) from None
    if parsed.tzinfo is None:
        message = f"Coinbase futures field {field!r} has no timezone."
        raise CoinbaseFuturesCatalogError(message)
    return parsed.astimezone(UTC)


def _required_instant(payload: Mapping[str, object], field: str) -> datetime:
    """Return one required RFC 3339 UTC instant."""
    value = _optional_instant(payload, field)
    if value is None:
        message = f"Coinbase futures field {field!r} is required."
        raise CoinbaseFuturesCatalogError(message)
    return value
