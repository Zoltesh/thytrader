"""Exact margin evaluation is required for durable account recovery, independently of I9."""

from dataclasses import replace
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal, localcontext

import pytest

from tests.alerts.test_alert_service import _service
from tests.alerts.test_futures_recovery_proof import proven_book
from tests.execution.test_paper_futures_books import _policy
from thytrader.alerts.models import AlertCheck, AlertCode
from thytrader.alerts.store import InMemoryAlertStore
from thytrader.alerts.supervision_futures import futures_safety_evidence
from thytrader.operator.futures_live_health import futures_live_health_component

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("prior_failure", [True, False])
@pytest.mark.parametrize(
    "boundary", ["default", "inexact", "overflow", "underflow", "policy", "ratio"]
)
async def test_unknown_margin_survives_restart_until_valid_recovery(
    boundary: str, prior_failure: bool
) -> None:
    """Fresh matched positions cannot erase unknown arithmetic or missing policy evidence."""
    book, account, now = await proven_book()
    policy = _policy()
    assert policy.futures is not None
    policy = policy.model_copy(
        update={"futures": policy.futures.model_copy(update={"live_derisk_margin_ratio": "2"})}
    )
    assert policy.futures is not None
    bad_policy = policy
    threshold = {
        "default": "20.000000000000000000000000001",
        "inexact": "20.000000001",
        "overflow": "9E100",
        "underflow": "1.111E-110",
    }.get(boundary, "20")
    if boundary == "policy":
        bad_policy = None
    elif boundary == "ratio":
        bad_policy = policy.model_copy(
            update={"futures": policy.futures.model_copy(update={"live_derisk_margin_ratio": None})}
        )
    alerts = InMemoryAlertStore()
    if prior_failure:
        evidence = futures_safety_evidence(
            (book,),
            account=None,
            policy=policy,
            now=now - timedelta(seconds=1),
            observation_failed=True,
        )
        await _service(alerts).apply(
            evidence.findings, evaluated=evidence.evaluated, now=now - timedelta(seconds=1)
        )
    with localcontext() as ctx:
        if boundary in {"inexact", "overflow", "underflow"}:
            ctx.prec, ctx.Emax, ctx.Emin = 10, 100, -100
            ctx.rounding = ROUND_DOWN
            for signal in ctx.traps:
                ctx.traps[signal] = False
        before = ctx.copy()
        evidence = futures_safety_evidence(
            (book,),
            account=replace(account, liquidation_threshold_usd=Decimal(threshold)),
            policy=bad_policy,
            now=now,
        )
        assert ctx.flags == before.flags
        assert ctx.traps == before.traps
    assert (
        AlertCheck(AlertCode.FUTURES_POSITION_MISMATCH, f"deployment:{book.deployment.id}")
        in evidence.evaluated
    )
    assert AlertCheck(AlertCode.FUTURES_MARGIN_LOW, "futures:live") not in evidence.evaluated
    await _service(alerts).apply(evidence.findings, evaluated=evidence.evaluated, now=now)
    assert {row.code for row in await alerts.list_open_alerts()} == {
        AlertCode.FUTURES_ACCOUNT_UNKNOWN
    }
    assert (await futures_live_health_component(alerts)).status.value == "failed"
    later = now + timedelta(seconds=1)
    recovered = futures_safety_evidence(
        (book,),
        account=replace(account, venue=replace(account.venue, observed_at=later)),
        policy=policy,
        now=later,
    )
    await _service(alerts).apply(recovered.findings, evaluated=recovered.evaluated, now=later)
    assert not await alerts.list_open_alerts()
    assert (await futures_live_health_component(alerts)).status.value == "healthy"


@pytest.mark.parametrize(
    ("amount", "severity"),
    [
        ("39.9999", "critical"),
        ("40", "critical"),
        ("40.0001", "warning"),
        ("79.9999", "warning"),
        ("80", None),
        ("80.0001", None),
    ],
)
async def test_margin_valid_classifications(amount: str, severity: str | None) -> None:
    """Warning and critical equality controls use exact unrounded evidence."""
    book, account, now = await proven_book()
    policy = _policy()
    assert policy.futures is not None
    policy = policy.model_copy(
        update={"futures": policy.futures.model_copy(update={"live_derisk_margin_ratio": "2"})}
    )
    evidence = futures_safety_evidence(
        (book,),
        account=replace(account, available_margin_usd=Decimal(amount)),
        policy=policy,
        now=now,
    )
    assert not any(f.code is AlertCode.FUTURES_ACCOUNT_UNKNOWN for f in evidence.findings)
    assert [
        f.severity.value for f in evidence.findings if f.code is AlertCode.FUTURES_MARGIN_LOW
    ] == ([] if severity is None else [severity])


@pytest.mark.parametrize("pending", [True, False])
async def test_confirmed_flat_does_not_require_held_margin_policy(pending: bool) -> None:
    """Flat account proof can recover blindness, but cannot erase a durable de-risk duty."""
    book, account, now = await proven_book()
    book = replace(
        book,
        position=None,
        positions=(),
        orders=(),
        intents=(),
        fills=(),
        deployment=replace(
            book.deployment,
            futures_derisk_pending=pending,
            futures_flat_confirmation_after=now - timedelta(seconds=1),
        ),
    )
    account = replace(
        account, venue=replace(account.venue, positions={}), liquidation_threshold_usd=Decimal(0)
    )
    evidence = futures_safety_evidence((book,), account=account, policy=None, now=now)
    assert AlertCheck(AlertCode.FUTURES_ACCOUNT_UNKNOWN, "futures:live") in evidence.evaluated
    assert (
        AlertCheck(AlertCode.FUTURES_MARGIN_LOW, "futures:live") in evidence.evaluated
    ) is not pending
    assert any(f.code is AlertCode.FUTURES_MARGIN_LOW for f in evidence.findings) is pending


async def test_local_flat_is_not_a_flat_account_margin_exemption() -> None:
    """A nonzero venue account retains margin supervision despite a local flat projection."""
    book, account, now = await proven_book()
    book = replace(book, position=None, positions=(), orders=(), fills=(), intents=())
    evidence = futures_safety_evidence((book,), account=account, policy=None, now=now)
    assert any(f.code is AlertCode.FUTURES_ACCOUNT_UNKNOWN for f in evidence.findings)
    assert not any(f.code is AlertCode.FUTURES_MARGIN_LOW for f in evidence.evaluated)
