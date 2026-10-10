"""Futures fee evidence: a GET of the FUTURE transaction summary (ADR 0128, slice P1-3)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from tests.exchanges.test_coinbase_fees import FeeStubCoinbaseClient, StubResponse
from thytrader.exchanges.coinbase import CoinbaseAccount
from thytrader.exchanges.coinbase_cfm_preview import FuturesFeePreview, FuturesFeePreviewError
from thytrader.exchanges.fees import FeeProfile
from thytrader.exchanges.read_errors import (
    ExchangeReadError,
    ExchangeReadFailure,
    ExchangeReadFailureKind,
    ExchangeReadOperation,
)
from thytrader.operator.cli import _parser, _query
from thytrader.operator.futures_fees import futures_fee_evidence

_SUMMARY = {
    "total_volume": 1200.5,
    "fee_tier": {"pricing_tier": "Futures", "taker_fee_rate": "0.0005", "maker_fee_rate": "0"},
}


class _RecordingClient(FeeStubCoinbaseClient):
    """Remember the transaction-summary arguments."""

    def __init__(self) -> None:
        super().__init__(_SUMMARY)
        self.calls: list[dict[str, Any]] = []

    def get_transaction_summary(self, **kwargs: Any) -> Any:
        """Record the filter and return the summary."""
        self.calls.append(kwargs)
        return StubResponse(_SUMMARY)


def test_adapter_reads_the_future_product_type_summary() -> None:
    """The futures fee tier is the FUTURE-filtered summary, parsed like spot."""
    client = _RecordingClient()
    profile = asyncio.run(CoinbaseAccount(client=client).get_futures_fee_profile())

    assert client.calls == [{"product_type": "FUTURE"}]
    assert profile.taker_fee_rate == Decimal("0.0005")
    assert profile.fee_tier == "Futures"


class _Portfolio:
    """A portfolio double whose futures fee read (and optional preview) succeeds or raises."""

    def __init__(
        self,
        outcome: FeeProfile | Exception,
        preview: FuturesFeePreview | Exception | None = None,
    ) -> None:
        self.outcome = outcome
        self.preview = preview
        self.previewed: list[str] = []

    async def preview_futures_fee(self, product_id: str) -> FuturesFeePreview:
        """Return or raise the configured preview outcome."""
        self.previewed.append(product_id)
        if self.preview is None or isinstance(self.preview, Exception):
            raise self.preview or AssertionError("no preview configured")
        return self.preview

    async def get_futures_fee_profile(self) -> FeeProfile:
        """Return or raise the configured outcome."""
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _failure(kind: ExchangeReadFailureKind) -> ExchangeReadError:
    """A typed futures fee read failure."""
    return ExchangeReadError(
        ExchangeReadFailure(operation=ExchangeReadOperation.FUTURES_FEES, kind=kind)
    )


def test_evidence_reports_rates_and_never_a_per_contract_fee() -> None:
    """Available evidence carries the reported rates; the per-contract fee is operator input."""
    profile = FeeProfile(
        taker_fee_rate=Decimal("0.0005"),
        maker_fee_rate=Decimal("0"),
        usd_volume_30d=Decimal("1200.5"),
        fee_tier="Futures",
        as_of=datetime(2026, 10, 10, tzinfo=UTC),
    )
    evidence = asyncio.run(futures_fee_evidence(_Portfolio(profile)))

    assert evidence.status == "available"
    assert (evidence.maker_fee_rate, evidence.taker_fee_rate) == ("0", "0.0005")
    assert evidence.fee_per_contract is None
    assert evidence.fee_per_contract_source == "operator_input"


def test_evidence_is_unavailable_without_numbers_when_the_read_fails() -> None:
    """Demo adapters are unsupported; any other failure is read_failed; nothing is zero."""
    unsupported = asyncio.run(
        futures_fee_evidence(_Portfolio(_failure(ExchangeReadFailureKind.UNSUPPORTED)))
    )
    failed = asyncio.run(futures_fee_evidence(_Portfolio(_failure(ExchangeReadFailureKind.HTTP))))
    broken = asyncio.run(futures_fee_evidence(_Portfolio(ValueError("bad"))))

    assert (unsupported.status, unsupported.unavailable_reason) == ("unavailable", "unsupported")
    assert failed.unavailable_reason == "read_failed"
    assert broken.unavailable_reason == "read_failed"
    assert unsupported.taker_fee_rate is None


_PROFILE = FeeProfile(
    taker_fee_rate=Decimal("0.0005"),
    maker_fee_rate=Decimal("0"),
    usd_volume_30d=Decimal("1200.5"),
    fee_tier="Futures",
    as_of=datetime(2026, 10, 10, tzinfo=UTC),
)


def test_no_preview_is_sent_unless_one_is_requested() -> None:
    """The default fees report never sends the orders/preview POST."""
    portfolio = _Portfolio(_PROFILE)
    evidence = asyncio.run(futures_fee_evidence(portfolio))

    assert portfolio.previewed == []
    assert evidence.fee_per_contract is None
    assert evidence.preview_product_id is None


def test_a_requested_preview_fills_the_per_contract_fee() -> None:
    """The quoted one-contract commission becomes fee_per_contract (orders_preview)."""
    preview = FuturesFeePreview(
        product_id="BIP-20DEC30-CDE",
        side="BUY",
        contracts=Decimal(1),
        commission_total=Decimal("0.15"),
        fee_per_contract=Decimal("0.15"),
        observed_at=datetime(2026, 10, 10, 1, tzinfo=UTC),
    )
    portfolio = _Portfolio(_PROFILE, preview)
    evidence = asyncio.run(futures_fee_evidence(portfolio, "BIP-20DEC30-CDE"))

    assert portfolio.previewed == ["BIP-20DEC30-CDE"]
    assert evidence.status == "available"
    assert evidence.fee_per_contract == "0.15"
    assert evidence.fee_per_contract_source == "orders_preview"
    assert evidence.preview_commission_total == "0.15"


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (FuturesFeePreviewError("preview_rejected"), "preview_rejected"),
        (_failure(ExchangeReadFailureKind.UNSUPPORTED), "unsupported"),
        (RuntimeError("boom"), "read_failed"),
    ],
)
def test_a_failed_preview_leaves_the_fee_unknown(failure: Exception, reason: str) -> None:
    """A refused or failed preview reports why and never a number."""
    evidence = asyncio.run(futures_fee_evidence(_Portfolio(_PROFILE, failure), "BIP-20DEC30-CDE"))

    assert evidence.fee_per_contract is None
    assert evidence.fee_per_contract_source == "operator_input"
    assert evidence.preview_unavailable_reason == reason


def test_cli_maps_the_preview_flag_onto_the_http_query() -> None:
    """``fees --futures-preview-product-id`` is the only way to request the probe."""
    parser = _parser()
    plain = parser.parse_args(["fees"])
    probing = parser.parse_args(["fees", "--futures-preview-product-id", "BIP-20DEC30-CDE"])

    assert "futures_preview_product_id" not in _query(plain)
    assert _query(probing)["futures_preview_product_id"] == "BIP-20DEC30-CDE"
