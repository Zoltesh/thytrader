"""The CFM fee probe: one allowlisted ``orders/preview`` POST (ADR 0128, slice P1-3b)."""

from __future__ import annotations

import asyncio
from decimal import Decimal
import inspect
from typing import TYPE_CHECKING, Any

import pytest

from thytrader.exchanges import coinbase_cfm_preview
from thytrader.exchanges.coinbase_cfm_preview import (
    CFM_PREVIEW_POST_ALLOWLIST,
    CoinbaseCfmFeePreview,
    FuturesFeePreviewError,
)
from thytrader.exchanges.rest_transport import CoinbaseHttpStatusError

if TYPE_CHECKING:
    from collections.abc import Mapping

_PREVIEW = {
    "preview_id": "ignored",
    "order_total": "1201.15",
    "commission_total": "0.15",
    "errs": [],
    "warning": [],
}


class _PostOnly:
    """A transport with ``post`` only; there is no ``get`` or delete to call."""

    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = _PREVIEW if response is None else response
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, path: str, data: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Record the request and answer it."""
        self.calls.append((path, dict(data or {})))
        if self.error is not None:
            raise self.error
        return self.response


def test_the_only_reachable_path_is_orders_preview() -> None:
    """No create, cancel, edit, close_position or GET path exists in the probe."""
    assert frozenset({"/api/v3/brokerage/orders/preview"}) == CFM_PREVIEW_POST_ALLOWLIST
    body = inspect.getsource(coinbase_cfm_preview).split('"""', 2)[2]
    forbidden_calls = (
        "_transport.get",
        "_transport.delete",
        "batch_cancel",
        "close_position",
        "/edit",
        "create_order",
    )
    for forbidden in forbidden_calls:
        assert forbidden not in body
    public = {
        name
        for name, _ in inspect.getmembers(CoinbaseCfmFeePreview, inspect.isfunction)
        if not name.startswith("_")
    }
    assert public == {"preview_fee"}


def test_a_path_outside_the_allowlist_is_refused_before_any_request() -> None:
    """The guard runs before the transport is touched."""
    transport = _PostOnly()
    probe = CoinbaseCfmFeePreview(transport)
    with pytest.raises(FuturesFeePreviewError) as caught:
        asyncio.run(probe._post("/api/v3/brokerage/orders", {}))
    assert caught.value.reason == "path_not_allowlisted"
    assert transport.calls == []


def test_one_contract_market_preview_reports_the_commission() -> None:
    """The fixed body previews one contract; the commission is the per-contract fee."""
    transport = _PostOnly()
    preview = asyncio.run(CoinbaseCfmFeePreview(transport).preview_fee("BIP-20DEC30-CDE"))

    assert transport.calls == [
        (
            "/api/v3/brokerage/orders/preview",
            {
                "product_id": "BIP-20DEC30-CDE",
                "side": "BUY",
                "order_configuration": {"market_market_ioc": {"base_size": "1"}},
            },
        )
    ]
    assert preview.fee_per_contract == Decimal("0.15")
    assert preview.contracts == Decimal(1)


@pytest.mark.parametrize(
    ("response", "error", "reason"),
    [
        ({**_PREVIEW, "errs": ["INSUFFICIENT_FUND"]}, None, "preview_rejected"),
        ({**_PREVIEW, "commission_total": "x"}, None, "malformed"),
        ({**_PREVIEW, "commission_total": "-1"}, None, "malformed"),
        (
            {**_PREVIEW, "commission_total": {"value": "1", "currency": "USDC"}},
            None,
            "non_usd_commission",
        ),
        ([], None, "malformed"),
        (None, CoinbaseHttpStatusError(403, "PERMISSION_DENIED"), "http_403"),
        (None, TimeoutError(), "transport"),
    ],
)
def test_failures_are_reason_tokens(response: object, error: Exception | None, reason: str) -> None:
    """A refused, malformed or failed preview never yields a number or response text."""
    probe = CoinbaseCfmFeePreview(_PostOnly(response, error))
    with pytest.raises(FuturesFeePreviewError) as caught:
        asyncio.run(probe.preview_fee("BIP-20DEC30-CDE"))
    assert caught.value.reason == reason


def test_spot_ids_are_refused_before_any_request() -> None:
    """Only CDE futures ids can be previewed."""
    transport = _PostOnly()
    with pytest.raises(FuturesFeePreviewError, match="not_a_futures_product"):
        asyncio.run(CoinbaseCfmFeePreview(transport).preview_fee("BTC-USD"))
    assert transport.calls == []
