"""Exact safety boundaries are independent of bounded display rounding and caller traps."""

from dataclasses import replace
from decimal import Decimal, Inexact, localcontext
from typing import TYPE_CHECKING

import pytest

from tests.alerts.test_futures_recovery_proof import proven_book
from tests.exchanges.test_coinbase_cfm import _transport
from tests.execution.test_paper_futures_books import _policy
from tests.operator_diagnostics.test_futures_account_report import _Store
from thytrader.exchanges.coinbase_cfm import CoinbaseCfmAccount
from thytrader.operator.futures_books_live import live_payload
from thytrader.operator.readiness_futures import futures_section

if TYPE_CHECKING:
    from thytrader.operator.readiness_models import ReadinessFinding
from thytrader.trading.models import FundingCashFlow
from thytrader.worker.futures_mirror import observe_futures_account

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("amount", ["1.0001", "1E10"])
@pytest.mark.parametrize("traps", [True, False])
async def test_live_funding_sum_is_unknown_when_inexact_or_overflow(
    amount: str, traps: bool
) -> None:
    """The adjacent new live funding total follows the same exact evidence boundary as margin."""
    book, account, now = await proven_book()
    book = replace(
        book,
        funding=(
            FundingCashFlow(
                deployment_id=book.deployment.id,
                product_id=book.deployment.product_id,
                funding_time=now,
                signed_quantity=Decimal(1),
                mark_price=Decimal(100),
                rate=Decimal(".01"),
                amount=Decimal(amount),
                applied_at=now,
            ),
        ),
    )
    with localcontext() as ctx:
        ctx.prec = 3
        ctx.Emax = 4
        for signal in ctx.traps:
            ctx.traps[signal] = traps
        ctx.clear_flags()
        before = ctx.copy()
        payload = live_payload(
            book, account=account, observation=None, policy=_policy(), books=(book,), now=now
        )
        assert payload.funding_accrued_usd is None
        assert ctx.flags == before.flags
        assert ctx.traps == before.traps


@pytest.mark.parametrize("available", [None, Decimal("NaN"), Decimal("Infinity")])
async def test_readiness_invalid_margin_is_explicitly_unknown(available: Decimal | None) -> None:
    """A present receipt with incomplete financial values cannot silently pass readiness."""
    book, _, now = await proven_book()
    receipt = await observe_futures_account(CoinbaseCfmAccount(_transport()), now)
    assert receipt.balance is not None
    receipt = replace(receipt, balance=replace(receipt.balance, available_margin=available))
    findings: list[ReadinessFinding] = []
    with localcontext() as ctx:
        ctx.traps[Inexact] = True
        await futures_section(
            _Store(receipt), findings, now=now, snapshots=(book,), policy=_policy()
        )
    assert any(f.reason_code == "FUTURES_MARGIN_UNKNOWN" for f in findings)


@pytest.mark.parametrize(
    ("available", "severity"),
    [
        ("39.9999", "violation"),
        ("40", "violation"),
        ("40.0001", "advisory"),
        ("79.9999", "advisory"),
        ("80", None),
        ("80.0001", None),
        ("-1", "violation"),
    ],
)
async def test_readiness_exact_margin_boundaries(available: str, severity: str | None) -> None:
    """Neither warning nor critical classification may use the four-place display ratio."""
    book, _, now = await proven_book()
    receipt = await observe_futures_account(CoinbaseCfmAccount(_transport()), now)
    assert receipt.balance is not None
    receipt = replace(
        receipt,
        balance=replace(
            receipt.balance, available_margin=Decimal(available), liquidation_threshold=Decimal(20)
        ),
    )
    policy = _policy()
    assert policy.futures is not None
    policy = policy.model_copy(
        update={"futures": policy.futures.model_copy(update={"live_derisk_margin_ratio": "2"})}
    )
    findings: list[ReadinessFinding] = []
    await futures_section(_Store(receipt), findings, now=now, snapshots=(book,), policy=policy)
    assert [f.severity.value for f in findings if f.reason_code == "FUTURES_MARGIN_LOW"] == (
        [] if severity is None else [severity]
    )


@pytest.mark.parametrize(
    ("available", "threshold"),
    [("1", "3"), ("1E10", "1"), ("NaN", "1"), ("1", "Infinity"), ("1", "0")],
)
@pytest.mark.parametrize("traps", [True, False])
async def test_display_ratio_contains_decimal_failures(
    available: str, threshold: str, traps: bool
) -> None:
    """Display projections contain inexact/overflow and preserve the caller's flags and traps."""
    book, account, now = await proven_book()
    account = replace(
        account,
        available_margin_usd=Decimal(available),
        liquidation_threshold_usd=Decimal(threshold),
    )
    with localcontext() as ctx:
        ctx.prec = 6
        ctx.Emax = 4
        ctx.traps[Inexact] = traps
        ctx.clear_flags()
        before = ctx.copy()
        payload = live_payload(
            book, account=account, observation=None, policy=_policy(), books=(book,), now=now
        )
        assert ctx.flags == before.flags
        assert ctx.traps == before.traps
        assert payload.margin_ratio is None or Decimal(payload.margin_ratio).is_finite()
        if available == "1" and threshold == "3":
            assert payload.margin_ratio is None or Decimal(payload.margin_ratio) > 0
