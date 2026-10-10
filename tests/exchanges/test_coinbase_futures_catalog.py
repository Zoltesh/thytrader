"""Coinbase CFM futures listing: strict FCM parsing, perp detection and paging (ADR 0126)."""

from __future__ import annotations

import asyncio
import copy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import pytest

from thytrader.exchanges.coinbase_futures_catalog import (
    FUTURES_LISTING_PAGE_SIZE,
    CoinbaseFuturesCatalog,
    CoinbaseFuturesCatalogError,
    parse_futures_row,
)
from thytrader.exchanges.coinbase_market_data import CoinbaseMarketDataError, _parse_product
from thytrader.market_data.instruments import InstrumentKind

_FIXTURE = Path(__file__).parent / "fixtures" / "coinbase_futures_listing.json"


def _rows() -> list[dict[str, Any]]:
    """Return fresh copies of the verbatim public-listing rows (8 FCM, 2 INTX)."""
    payload: dict[str, Any] = json.loads(_FIXTURE.read_text())
    return copy.deepcopy(payload["products"])


def _row(product_id: str) -> dict[str, Any]:
    """Return one fixture row by id."""
    return next(row for row in _rows() if row["product_id"] == product_id)


class _Response:
    """Mimic the SDK response object's ``to_dict``."""

    def __init__(self, payload: dict[str, Any]) -> None:
        """Hold one JSON page."""
        self._payload = payload

    def to_dict(self) -> dict[str, Any]:
        """Return the page as untrusted plain data."""
        return self._payload


class _Client:
    """Serve scripted listing pages and record each request's paging arguments."""

    def __init__(self, pages: list[dict[str, Any]] | None = None, *, fail: bool = False) -> None:
        """Script pages in request order, or fail every call like a transport error."""
        self.pages = pages or []
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def get_public_products(
        self,
        limit: int | None = None,
        offset: int | None = None,
        product_type: str | None = None,
        product_ids: list[str] | None = None,
        contract_expiry_type: str | None = None,
        expiring_contract_status: str | None = None,
        get_all_products: bool = False,
    ) -> _Response:
        """Return the next scripted page; only paging and type filters may be sent."""
        assert product_ids is None
        assert contract_expiry_type is None
        assert expiring_contract_status is None
        assert get_all_products is False
        self.calls.append({"limit": limit, "offset": offset, "product_type": product_type})
        if self.fail:
            raise ConnectionError("https://api.coinbase.com/secret-looking-url")
        return _Response(self.pages[len(self.calls) - 1])


def _page(rows: list[dict[str, Any]], *, has_next: bool | None) -> dict[str, Any]:
    """Build one listing page; ``has_next=None`` omits pagination entirely."""
    page: dict[str, Any] = {"products": rows, "num_products": len(rows)}
    if has_next is not None:
        page["pagination"] = {"has_next": has_next, "next_cursor": "", "prev_cursor": ""}
    return page


def _list(client: _Client) -> tuple[Any, ...]:
    """Run the adapter's listing on a fresh loop."""
    return asyncio.run(CoinbaseFuturesCatalog(client).list_futures_products())


def test_fixture_listing_parses_fcm_rows_and_excludes_intx() -> None:
    """All eight FCM rows parse; retired INTX perps are dropped, not errors."""
    client = _Client([_page(_rows(), has_next=False)])
    products = _list(client)
    assert [p.product_id for p in products] == sorted(
        [
            "BIP-20DEC30-CDE",
            "BIT-30OCT26-CDE",
            "ET-30OCT26-CDE",
            "ETP-20DEC30-CDE",
            "GOL-25NOV26-CDE",
            "NGS-27OCT26-CDE",
            "SLP-20DEC30-CDE",
            "US5-19DEC30-CDE",
        ]
    )
    assert client.calls == [
        {"limit": FUTURES_LISTING_PAGE_SIZE, "offset": 0, "product_type": "FUTURE"}
    ]


def test_perps_are_detected_by_funding_interval_not_expiry_type() -> None:
    """The venue says EXPIRING for every row; a funding interval marks a perp."""
    by_id = {p.product_id: p for p in _list(_Client([_page(_rows(), has_next=False)]))}
    perps = {pid for pid, p in by_id.items() if p.kind is InstrumentKind.PERPETUAL_FUTURE}
    assert perps == {"BIP-20DEC30-CDE", "ETP-20DEC30-CDE", "SLP-20DEC30-CDE", "US5-19DEC30-CDE"}
    bip = by_id["BIP-20DEC30-CDE"]
    assert bip.funding is not None
    assert bip.funding.interval == timedelta(hours=1)
    assert bip.funding.rate == Decimal("0.000009")
    assert bip.funding.funding_time == datetime(2026, 10, 9, 23, tzinfo=UTC)
    assert by_id["BIT-30OCT26-CDE"].funding is None


def test_perp_expiry_sentinel_is_not_reported_as_an_expiry() -> None:
    """``2089-12-30`` stays a raw venue fact; the id date is kept separately."""
    by_id = {p.product_id: p for p in _list(_Client([_page(_rows(), has_next=False)]))}
    bip = by_id["BIP-20DEC30-CDE"]
    assert bip.venue_expiry_at == datetime(2089, 12, 30, 16, tzinfo=UTC)
    assert bip.expires_at is None
    assert bip.listed_expiry == date(2030, 12, 20)
    bit = by_id["BIT-30OCT26-CDE"]
    assert bit.expires_at == datetime(2026, 10, 30, 16, tzinfo=UTC)
    assert bit.listed_expiry == date(2026, 10, 30)


def test_underlying_comes_from_contract_root_unit_not_the_id_prefix() -> None:
    """BIP is BTC and SLP is SOL; an index perp keeps its venue root."""
    by_id = {p.product_id: p for p in _list(_Client([_page(_rows(), has_next=False)]))}
    assert by_id["BIP-20DEC30-CDE"].underlying == "BTC"
    assert by_id["BIP-20DEC30-CDE"].contract_code == "BIP"
    assert by_id["SLP-20DEC30-CDE"].underlying == "SOL"
    assert by_id["US5-19DEC30-CDE"].underlying == "CDEUS5"
    assert by_id["ETP-20DEC30-CDE"].contract_size == Decimal("0.1")


def test_futures_constraints_margin_and_sessions_are_exact() -> None:
    """Contract-unit sizes, margin pairs, 24/7 flags and maintenance windows parse exactly."""
    by_id = {p.product_id: p for p in _list(_Client([_page(_rows(), has_next=False)]))}
    bip = by_id["BIP-20DEC30-CDE"]
    assert (bip.base_increment, bip.base_min_size, bip.price_increment) == (
        Decimal(1),
        Decimal(1),
        Decimal(5),
    )
    assert bip.settlement_currency == "USD"
    assert bip.overnight_margin is not None
    assert bip.overnight_margin.short == Decimal("0.256625")
    assert bip.twenty_four_by_seven is True
    assert bip.session.maintenance is not None
    assert bip.session.maintenance.starts_at == datetime(2026, 10, 24, 10, tzinfo=UTC)
    assert by_id["US5-19DEC30-CDE"].twenty_four_by_seven is False
    assert by_id["US5-19DEC30-CDE"].session.is_open is False
    assert by_id["NGS-27OCT26-CDE"].session.maintenance is None


def test_spot_parser_still_rejects_futures_rows() -> None:
    """Empty base and zero quote_min_size keep futures out of every spot surface."""
    row = _row("BIP-20DEC30-CDE")
    with pytest.raises(CoinbaseMarketDataError):
        _parse_product(row)
    row["base_currency_id"] = "BTC"
    with pytest.raises(CoinbaseMarketDataError):
        _parse_product(row)
    parsed = parse_futures_row(_row("BIP-20DEC30-CDE"))
    assert parsed is not None


def _clone(index: int) -> dict[str, Any]:
    """Return a distinct valid perp row for paging tests."""
    row = _row("BIP-20DEC30-CDE")
    row["product_id"] = f"X{index:02d}-20DEC30-CDE"
    return row


def test_listing_pages_by_offset_until_has_next_is_false() -> None:
    """A full first page and a short final page are both read and merged."""
    first = [_clone(i) for i in range(FUTURES_LISTING_PAGE_SIZE)]
    client = _Client([_page(first, has_next=True), _page(_rows(), has_next=False)])
    products = _list(client)
    assert len(products) == FUTURES_LISTING_PAGE_SIZE + 8
    assert [call["offset"] for call in client.calls] == [0, FUTURES_LISTING_PAGE_SIZE]


@pytest.mark.parametrize(
    ("pages", "reason"),
    [
        ([_page([], has_next=False)], "empty"),
        ([_page([_clone(i) for i in range(FUTURES_LISTING_PAGE_SIZE)], has_next=None)], "full"),
        (
            [_page([_clone(i) for i in range(FUTURES_LISTING_PAGE_SIZE + 1)], has_next=False)],
            "size",
        ),
        ([_page([], has_next=True)], "empty page"),
        ([_page([_clone(1)], has_next=True), _page([_clone(1)], has_next=False)], "repeated"),
        ([{"products": None}], "product list"),
    ],
)
def test_possibly_truncated_listings_fail_closed(pages: list[dict[str, Any]], reason: str) -> None:
    """A listing whose coverage cannot be proved is an error, never a short catalog."""
    with pytest.raises(CoinbaseFuturesCatalogError, match=reason):
        _list(_Client(pages))


def test_listing_that_never_ends_fails_closed() -> None:
    """``has_next`` forever exhausts the page budget instead of looping."""
    pages = [_page([_clone(i)], has_next=True) for i in range(40)]
    with pytest.raises(CoinbaseFuturesCatalogError, match="page budget"):
        _list(_Client(pages))


def test_transport_failure_drops_provider_detail() -> None:
    """The SDK error text (which can echo URLs) never reaches the raised error."""
    with pytest.raises(CoinbaseFuturesCatalogError) as caught:
        _list(_Client(fail=True))
    assert "secret-looking-url" not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    ("field_path", "value"),
    [
        (("quote_currency_id",), "USDC"),
        (("product_id",), "BTC-USD"),
        (("product_type",), "SPOT"),
        (("future_product_details", "funding_interval"), "1h"),
        (("future_product_details", "contract_size"), "0"),
        (("future_product_details", "contract_root_unit"), ""),
        (("future_product_details", "twenty_four_by_seven"), "true"),
        (("future_product_details", "contract_expiry"), "2030-12-20"),
        (("base_min_size",), "0"),
    ],
)
def test_malformed_fcm_rows_fail_closed(field_path: tuple[str, ...], value: object) -> None:
    """Every required futures fact is validated; one bad row fails the listing."""
    row = _row("BIP-20DEC30-CDE")
    target: dict[str, Any] = row
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = value
    with pytest.raises(CoinbaseFuturesCatalogError):
        parse_futures_row(row)


def test_unlisted_margin_rates_are_unknown_not_zero() -> None:
    """An empty margin pair is ``None`` (unknown); it never becomes a zero rate."""
    row = _row("ETP-20DEC30-CDE")
    row["future_product_details"]["intraday_margin_rate"] = {
        "long_margin_rate": "",
        "short_margin_rate": "",
    }
    parsed = parse_futures_row(row)
    assert parsed is not None
    assert parsed.intraday_margin is None
    assert parsed.overnight_margin is not None


def test_disabled_contract_is_listed_but_not_enabled() -> None:
    """A venue-disabled contract parses with ``trading_enabled`` false."""
    row = _row("ETP-20DEC30-CDE")
    row["trading_disabled"] = True
    parsed = parse_futures_row(row)
    assert parsed is not None
    assert parsed.trading_enabled is False
