"""Behavioral tests for the Coinbase fee tier adapter and domain mapping."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
import pytest

from thytrader.exchanges.coinbase import CoinbaseAccount
from thytrader.exchanges.fees import FeeProfile


class StubResponse:
    """Coinbase SDK response exposing its dictionary conversion."""

    def __init__(self, payload: dict[str, Any]) -> None:
        """Initialize with payload dictionary."""
        self._payload = payload

    def to_dict(self) -> dict[str, Any]:
        """Convert payload to dict."""
        return dict(self._payload)


class FeeStubCoinbaseClient:
    """Stub client exposing get_transaction_summary responses."""

    def __init__(self, summary_payload: dict[str, Any]) -> None:
        """Initialize with test summary payload."""
        self._summary_payload = summary_payload

    def get_accounts(self, *, limit: int, cursor: str | None = None) -> Any:
        """Return empty accounts."""
        del limit, cursor
        return StubResponse({"accounts": [], "has_next": False})

    def get_api_key_permissions(self) -> Any:
        """Return view permission."""
        return StubResponse({"can_view": True})

    def get_product(self, product_id: str) -> Any:
        """Return dummy price."""
        del product_id
        return StubResponse({"price": "100.00"})

    def get_transaction_summary(self, **kwargs: Any) -> Any:
        """Return stubbed response."""
        del kwargs
        return StubResponse(self._summary_payload)


def test_fee_profile_domain_validation() -> None:
    """FeeProfile requires Decimal rates in [0, 1] and positive volume."""
    valid = FeeProfile(
        taker_fee_rate=Decimal("0.0060"),
        maker_fee_rate=Decimal("0.0040"),
        usd_volume_30d=Decimal("50000.00"),
        fee_tier="Tier 1",
        as_of=datetime.now(UTC),
        source="coinbase",
    )
    assert valid.taker_fee_rate == Decimal("0.0060")

    with pytest.raises(ValidationError):
        FeeProfile(
            taker_fee_rate=Decimal("-0.01"),
            maker_fee_rate=Decimal("0.0040"),
            usd_volume_30d=Decimal("50000.00"),
            fee_tier="Tier 1",
            as_of=datetime.now(UTC),
            source="coinbase",
        )

    with pytest.raises(ValidationError):
        FeeProfile(
            taker_fee_rate=Decimal("1.5"),
            maker_fee_rate=Decimal("0.0040"),
            usd_volume_30d=Decimal("50000.00"),
            fee_tier="Tier 1",
            as_of=datetime.now(UTC),
            source="coinbase",
        )


def test_coinbase_adapter_parses_fee_summary() -> None:
    """Adapter maps SDK get_transaction_summary response into typed FeeProfile."""
    payload = {
        "total_volume": 125000.50,
        "total_fees": 450.00,
        "fee_tier": {
            "pricing_tier": "Tier 2 ($10k-$50k)",
            "taker_fee_rate": "0.0040",
            "maker_fee_rate": "0.0025",
        },
    }
    client = FeeStubCoinbaseClient(payload)
    adapter = CoinbaseAccount(client)

    profile = asyncio.run(adapter.get_fee_profile())

    assert profile.fee_tier == "Tier 2 ($10k-$50k)"
    assert profile.taker_fee_rate == Decimal("0.0040")
    assert profile.maker_fee_rate == Decimal("0.0025")
    assert profile.usd_volume_30d == Decimal("125000.5")
    assert profile.source == "coinbase"
    assert profile.as_of.tzinfo == UTC


def test_coinbase_adapter_rejects_missing_fee_summary_fields() -> None:
    """An empty Coinbase response must not fabricate zero-rate fee evidence."""
    adapter = CoinbaseAccount(FeeStubCoinbaseClient({}))

    with pytest.raises(ValueError, match="fee"):
        asyncio.run(adapter.get_fee_profile())


def test_coinbase_adapter_rejects_whitespace_only_fee_tier() -> None:
    """A whitespace fee tier label is missing rather than authoritative evidence."""
    adapter = CoinbaseAccount(
        FeeStubCoinbaseClient(
            {
                "total_volume": "0",
                "fee_tier": {
                    "pricing_tier": "   ",
                    "taker_fee_rate": "0.006",
                    "maker_fee_rate": "0.004",
                },
            }
        )
    )

    with pytest.raises(ValueError, match="fee tier name"):
        asyncio.run(adapter.get_fee_profile())
