"""Futures fee evidence: a GET of the FUTURE transaction summary (ADR 0128, slice P1-3)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import pytest

from tests.exchanges.test_coinbase_fees import FeeStubCoinbaseClient, StubResponse
from thytrader.exchanges.coinbase import CoinbaseAccount
from thytrader.exchanges.coinbase_cfm_preview import FuturesFeePreview, FuturesFeePreviewError
from thytrader.exchanges.coinbase_futures_catalog import parse_futures_row
from thytrader.exchanges.fees import FeeProfile
from thytrader.exchanges.read_errors import (
    ExchangeReadError,
    ExchangeReadFailure,
    ExchangeReadFailureKind,
    ExchangeReadOperation,
)
from thytrader.market_data.instruments import Instrument
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


# An Intro futures tier (0.10% taker) and the ETP perp's 0.1 ETH contract.
_INTRO = FeeProfile(
    taker_fee_rate=Decimal("0.001"),
    maker_fee_rate=Decimal("0.00095"),
    usd_volume_30d=Decimal("0"),
    fee_tier="Intro",
    as_of=datetime(2026, 10, 10, tzinfo=UTC),
)
_ETP = "ETP-20DEC30-CDE"
_FIXTURE = Path(__file__).parents[1] / "exchanges" / "fixtures" / "coinbase_futures_listing.json"


def _preview(total: str, price: str | None = None, fixed: str | None = None) -> FuturesFeePreview:
    """A one-contract ETP preview with an all-in commission."""
    return FuturesFeePreview(
        product_id=_ETP,
        side="BUY",
        contracts=Decimal(1),
        commission_total=Decimal(total),
        price=None if price is None else Decimal(price),
        fixed_commission=None if fixed is None else Decimal(fixed),
        observed_at=datetime(2026, 10, 10, 19, tzinfo=UTC),
    )


class _Catalog:
    """The listing fixture's ETP perp (contract size 0.1 ETH), or a failing read."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def enabled_instrument(self, product_id: str) -> Instrument | None:
        """Return the fixture contract for ``product_id``."""
        if self.fail:
            raise RuntimeError("catalog down")
        rows: list[dict[str, Any]] = json.loads(_FIXTURE.read_text())["products"]
        parsed = (parse_futures_row(row) for row in rows)
        found = next((p for p in parsed if p is not None and p.product_id == product_id), None)
        if found is None:
            return None
        return Instrument(product_id=found.product_id, kind=found.kind, future=found)


def test_the_fixed_fee_is_the_all_in_commission_less_the_taker_rate() -> None:
    """0.361 all-in at 2510 = 0.001 x 0.1 x 2510 + 0.11, so fee_per_contract is 0.11."""
    portfolio = _Portfolio(_INTRO, _preview("0.361", price="2510"))
    evidence = asyncio.run(futures_fee_evidence(portfolio, _ETP, _Catalog()))

    assert portfolio.previewed == [_ETP]
    assert evidence.preview_contract_size == "0.1"
    assert evidence.preview_price == "2510"
    assert evidence.preview_rate_commission == "0.251"
    assert evidence.preview_commission_total == "0.361"
    assert evidence.fee_per_contract == "0.11"
    assert evidence.fee_per_contract_source == "orders_preview_less_taker_rate"
    assert evidence.fee_per_contract_unavailable_reason is None


def test_another_price_gives_the_same_fixed_fee() -> None:
    """0.36055 at 2505.5 also leaves exactly 0.11 per contract."""
    evidence = asyncio.run(
        futures_fee_evidence(_Portfolio(_INTRO, _preview("0.36055", "2505.5")), _ETP, _Catalog())
    )
    assert evidence.fee_per_contract == "0.11"


def test_an_itemized_fixed_commission_wins_over_the_rounded_total() -> None:
    """A preview rounds the total to 0.36; its venue + clearing lines are exactly 0.11."""
    preview = _preview("0.36", price="2505.5", fixed="0.11")
    evidence = asyncio.run(futures_fee_evidence(_Portfolio(_INTRO, preview), _ETP, _Catalog()))

    assert evidence.fee_per_contract == "0.11"
    assert evidence.fee_per_contract_source == "orders_preview_itemized"
    assert evidence.preview_fixed_commission_itemized == "0.11"
    assert evidence.preview_rate_commission == "0.25055"
    assert evidence.preview_commission_total == "0.36"


def test_the_itemized_fixed_commission_needs_no_price_or_catalog() -> None:
    """Coinbase's own itemization stands alone; the rate split is then just context."""
    preview = _preview("0.36", fixed="0.11")
    evidence = asyncio.run(futures_fee_evidence(_Portfolio(_INTRO, preview), _ETP))

    assert evidence.fee_per_contract == "0.11"
    assert evidence.preview_rate_commission is None


@pytest.mark.parametrize(
    ("profile", "preview", "catalog", "reason"),
    [
        (
            _failure(ExchangeReadFailureKind.HTTP),
            _preview("0.36", "2505.5"),
            _Catalog(),
            "taker_fee_rate_unavailable",
        ),
        (_INTRO, _preview("0.36"), _Catalog(), "preview_price_unavailable"),
        (_INTRO, _preview("0.36", "2505.5"), None, "contract_size_unavailable"),
        (_INTRO, _preview("0.36", "2505.5"), _Catalog(fail=True), "contract_size_unavailable"),
        (_INTRO, _preview("0.2", "2505.5"), _Catalog(), "negative_fixed_fee"),
    ],
)
def test_an_underivable_fixed_fee_is_null_with_a_reason(
    profile: FeeProfile | Exception,
    preview: FuturesFeePreview,
    catalog: _Catalog | None,
    reason: str,
) -> None:
    """Without the rate, price or contract size, or with a negative remainder, never a number."""
    evidence = asyncio.run(futures_fee_evidence(_Portfolio(profile, preview), _ETP, catalog))

    assert evidence.fee_per_contract is None
    assert evidence.fee_per_contract_source == "operator_input"
    assert evidence.fee_per_contract_unavailable_reason == reason
    assert evidence.preview_commission_total == format(preview.commission_total, "f")
    assert evidence.preview_unavailable_reason is None


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
