"""Live spot collateral gate beside manual CFM futures (ADR 0129, slice P1-2a)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from tests.exchanges.test_coinbase_cfm import _transport
from tests.risk.test_live_capital_scope import _NOW, _observation, _snapshot
from thytrader.exchanges.coinbase_cfm import CoinbaseCfmAccount
from thytrader.exchanges.futures_models import (
    FuturesAccountObservation,
    FuturesAccountStoreUnavailableError,
    FuturesBalanceSummary,
    FuturesEnablement,
    FuturesMarginWindow,
)
from thytrader.execution.loop import _entry_verdict
from thytrader.operator.futures_collateral_report import futures_collateral_payload
from thytrader.risk.futures_collateral import (
    FuturesCollateralEvidence,
    FuturesCollateralState,
    classify_futures_collateral,
    collateral_verdict,
    load_futures_collateral,
    risk_futures_account_scope,
)
from thytrader.risk.futures_policy import FuturesRiskPolicy
from thytrader.risk.gate import evaluate_new_entry
from thytrader.risk.gate_common import ProposedEntry
from thytrader.risk.models import (
    RiskDecision,
    RiskPolicyDefinition,
    RiskReasonCode,
    RiskVerdict,
    compiled_default_risk_policy,
    risk_policy_fingerprint,
)
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode, DeploymentSnapshot
from thytrader.worker.futures_mirror import observe_futures_account


def _usd(value: str) -> Decimal:
    """An exact USD figure."""
    return Decimal(value)


def _live_account_idle() -> FuturesAccountObservation:
    """The live account on 2026-10-10: enabled, every read OK, flat, USDC is collateral."""
    balance = FuturesBalanceSummary(
        futures_buying_power=_usd("514.24"),
        total_usd_balance=_usd("0.01"),
        cbi_usd_balance=_usd("0.01"),
        cfm_usd_balance=_usd("0"),
        total_open_orders_hold_amount=_usd("0"),
        unrealized_pnl=_usd("0"),
        daily_realized_pnl=_usd("0"),
        initial_margin=_usd("0"),
        available_margin=_usd("514.24"),
        liquidation_threshold=_usd("0"),
        liquidation_buffer_amount=_usd("514.24"),
        liquidation_buffer_percentage=None,
        total_pending_transfers_amount=_usd("0"),
        funding_pnl=_usd("0"),
        intraday_margin=None,
        overnight_margin=None,
    )
    return FuturesAccountObservation(
        observed_at=_NOW - timedelta(seconds=30),
        enablement=FuturesEnablement.ENABLED,
        balance=balance,
        positions=(),
        intraday_margin_setting="INTRADAY_MARGIN_SETTING_STANDARD",
        margin_window=FuturesMarginWindow("MARGIN_WINDOW_TYPE_UNSPECIFIED", None, False, False),
        read_failures=(),
    )


def _in_use() -> FuturesAccountObservation:
    """One ETP short with 75.10 USD initial margin (documented fixtures); sync callers only."""
    return asyncio.run(_observe_in_use())


class _Store:
    """Serve one snapshot, or fail like unreadable storage."""

    def __init__(self, latest: FuturesAccountObservation | None, *, fail: bool = False) -> None:
        """Hold the canned answer."""
        self._latest = latest
        self.fail = fail

    async def record(self, observation: FuturesAccountObservation) -> None:
        """Unused."""
        del observation
        raise AssertionError("read-only")

    async def latest(self) -> FuturesAccountObservation | None:
        """Return the canned snapshot."""
        if self.fail:
            raise FuturesAccountStoreUnavailableError("down")
        return self._latest


def _state(latest: FuturesAccountObservation | None) -> FuturesCollateralState:
    """Classify at the fixed clock."""
    return classify_futures_collateral(latest, as_of=_NOW).state


def test_the_live_account_today_is_idle() -> None:
    """Enabled, flat, zero margin and hold, fresh: idle, so nothing changes."""
    assert _state(_live_account_idle()) is FuturesCollateralState.IDLE


def test_classification_table() -> None:
    """absent, in_use and unknown follow ADR 0129 section 2."""
    idle = _live_account_idle()
    assert _state(None) is FuturesCollateralState.ABSENT
    assert (
        _state(replace(idle, enablement=FuturesEnablement.NOT_ENABLED))
        is FuturesCollateralState.ABSENT
    )
    assert _state(_in_use()) is FuturesCollateralState.IN_USE
    assert idle.balance is not None
    margin_only = replace(idle, balance=replace(idle.balance, initial_margin=_usd("1")))
    assert _state(margin_only) is FuturesCollateralState.IN_USE
    hold_only = replace(
        idle, balance=replace(idle.balance, total_open_orders_hold_amount=_usd("0.5"))
    )
    assert _state(hold_only) is FuturesCollateralState.IN_USE
    stale = replace(idle, observed_at=_NOW - timedelta(minutes=4))
    assert _state(stale) is FuturesCollateralState.UNKNOWN
    assert _state(replace(idle, positions=None)) is FuturesCollateralState.UNKNOWN
    assert _state(replace(idle, balance=None)) is FuturesCollateralState.UNKNOWN
    assert (
        _state(replace(idle, enablement=FuturesEnablement.UNKNOWN))
        is FuturesCollateralState.UNKNOWN
    )


def _evidence(latest: FuturesAccountObservation | None) -> FuturesCollateralEvidence:
    """Classified evidence at the fixed clock."""
    return classify_futures_collateral(latest, as_of=_NOW)


def _policy(**futures: str) -> RiskPolicyDefinition:
    """A live policy, optionally with the futures block."""
    policy = compiled_default_risk_policy()
    if futures:
        return policy.model_copy(update={"futures": FuturesRiskPolicy.model_validate(futures)})
    return policy


def _verdict(
    policy: RiskPolicyDefinition,
    evidence: FuturesCollateralEvidence | None,
    *,
    product: str = "BTC-USD",
    notional: str = "10",
    mode: DeploymentMode = DeploymentMode.LIVE,
    cash: str = "100",
) -> RiskVerdict:
    """Run the whole gate for one entry."""
    return evaluate_new_entry(
        policy,
        mode=mode,
        proposed=ProposedEntry(product, uuid4(), Decimal(notional)),
        snapshots=(_snapshot(),),
        live_quote_cash=Decimal(cash),
        observation=_observation(),
        futures_collateral=evidence,
    )


@pytest.mark.parametrize("product", ["BTC-USD", "ETH-USD", "BTC-USDC"])
@pytest.mark.parametrize("notional", ["1", "10", "60", "101"])
@pytest.mark.parametrize("fraction", ["1", "0.5", "0.1"])
def test_idle_and_absent_change_no_verdict(product: str, notional: str, fraction: str) -> None:
    """For the live account's idle state, every verdict equals the gate without futures."""
    policy = _policy().model_copy(update={"max_portfolio_exposure_fraction": fraction})
    baseline = _verdict(policy, None, product=product, notional=notional)
    for latest in (_live_account_idle(), None):
        assert _verdict(policy, _evidence(latest), product=product, notional=notional) == baseline


def test_idle_leaves_capital_untouched() -> None:
    """The gate passes the venue quote through unchanged when idle."""
    verdict, cash = collateral_verdict(
        _policy(live_spot_collateral_reserve_quote="40"),
        mode=DeploymentMode.LIVE,
        proposed=ProposedEntry("BTC-USDC", None, Decimal(5)),
        evidence=_evidence(_live_account_idle()),
        live_quote_cash=Decimal("100"),
    )
    assert (verdict, cash) == (None, Decimal("100"))


def test_unknown_denies_live_usd_and_usdc_entries() -> None:
    """L1: a stale snapshot or failed read pauses live spot entries."""
    stale = _evidence(replace(_live_account_idle(), observed_at=_NOW - timedelta(minutes=5)))
    for product in ("BTC-USD", "BTC-USDC"):
        verdict = _verdict(_policy(), stale, product=product)
        assert verdict.reason_code is RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN


def test_in_use_denies_by_default() -> None:
    """L2: manual futures margin pauses live spot entries without a declared reserve."""
    verdict = _verdict(_policy(), _evidence(_in_use()))
    assert verdict.reason_code is RiskReasonCode.FUTURES_COLLATERAL_IN_USE
    assert "live_spot_collateral_reserve_quote" in verdict.detail


def test_reserve_must_cover_margin_times_haircut() -> None:
    """L3: 75.10 USD margin x 1.25 = 93.875 USD; a 90 reserve is short, 94 is enough."""
    in_use = _evidence(_in_use())
    short = _verdict(_policy(live_spot_collateral_reserve_quote="90"), in_use)
    assert short.reason_code is RiskReasonCode.FUTURES_COLLATERAL_RESERVE_SHORT
    assert "75.10 USD" in short.detail
    assert "90 USD" in short.detail
    allowed = _verdict(_policy(live_spot_collateral_reserve_quote="94"), in_use, notional="5")
    assert allowed.decision is RiskDecision.ALLOW
    strict = _verdict(_policy(live_spot_collateral_reserve_quote="94", peg_haircut="1.5"), in_use)
    assert strict.reason_code is RiskReasonCode.FUTURES_COLLATERAL_RESERVE_SHORT


def test_reserve_is_withheld_from_spot_capital() -> None:
    """With a 94 reserve, 100 available leaves 6 of capital: a 7 entry exceeds a full cap."""
    in_use = _evidence(_in_use())
    policy = _policy(live_spot_collateral_reserve_quote="94")
    assert _verdict(policy, in_use, notional="6").decision is RiskDecision.ALLOW
    denied = _verdict(policy, in_use, notional="7")
    assert denied.reason_code is RiskReasonCode.PORTFOLIO_EXPOSURE_EXCEEDED
    assert _verdict(policy, None, notional="7").decision is RiskDecision.ALLOW
    over = _verdict(policy, in_use, cash="50")
    assert over.reason_code is RiskReasonCode.FUTURES_COLLATERAL_RESERVE_SHORT


def test_unknown_margin_cannot_satisfy_a_reserve() -> None:
    """In use with an unknown initial margin is unknown, never zero."""
    latest = _in_use()
    assert latest.balance is not None
    unknown_margin = replace(latest, balance=replace(latest.balance, initial_margin=None))
    verdict = _verdict(
        _policy(live_spot_collateral_reserve_quote="1000"), _evidence(unknown_margin)
    )
    assert verdict.reason_code is RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN


def test_paper_usdt_and_in_kind_entries_are_not_gated() -> None:
    """Only live, quote-funded USD/USDC entries draw on the shared pool."""
    in_use = _evidence(_in_use())
    policy = _policy()
    assert _verdict(policy, in_use, mode=DeploymentMode.PAPER).reason_code is not (
        RiskReasonCode.FUTURES_COLLATERAL_IN_USE
    )
    verdict, _ = collateral_verdict(
        policy,
        mode=DeploymentMode.LIVE,
        proposed=ProposedEntry("BTC-USDT", None, Decimal(5)),
        evidence=in_use,
        live_quote_cash=Decimal(100),
    )
    assert verdict is None
    in_kind, _ = collateral_verdict(
        policy,
        mode=DeploymentMode.LIVE,
        proposed=ProposedEntry("BTC-USD", None, Decimal(5), funding="in_kind"),
        evidence=in_use,
        live_quote_cash=Decimal(100),
    )
    assert in_kind is None


@pytest.mark.anyio
async def test_loader_reads_the_bound_store_and_fails_closed() -> None:
    """Live loads classify the newest snapshot; unreadable storage is unknown; paper skips."""
    assert await load_futures_collateral(None, mode=DeploymentMode.PAPER, as_of=_NOW) is None
    absent = await load_futures_collateral(None, mode=DeploymentMode.LIVE, as_of=_NOW)
    assert absent is not None
    assert absent.state is FuturesCollateralState.ABSENT
    broken = await load_futures_collateral(
        _Store(None, fail=True), mode=DeploymentMode.LIVE, as_of=_NOW
    )
    assert broken is not None
    assert broken.state is FuturesCollateralState.UNKNOWN


@pytest.mark.anyio
async def test_worker_entry_admission_uses_the_bound_mirror() -> None:
    """The execution worker path denies a live entry while bound manual futures are in use."""
    probe = _snapshot()
    store = InMemoryExecutionStore()
    await store.create_deployment(probe.deployment)
    in_use = await _observe_in_use()
    unbound = await _admit(probe, store)
    assert unbound.decision is RiskDecision.ALLOW
    with risk_futures_account_scope(_Store(in_use)):
        bound = await _admit(probe, store)
    assert bound.reason_code is RiskReasonCode.FUTURES_COLLATERAL_IN_USE
    with risk_futures_account_scope(_Store(_live_account_idle())):
        idle = await _admit(probe, store)
    assert idle == unbound


async def _admit(probe: DeploymentSnapshot, store: InMemoryExecutionStore) -> RiskVerdict:
    """Admit one 7-quote BTC-USD live entry through the worker path."""
    return await _entry_verdict(
        probe,
        store=store,
        product_id="BTC-USD",
        notional=Decimal("7"),
        risk_policy=_policy(),
        portfolio=(),
        observation=_observation(),
    )


def test_policy_without_futures_keeps_its_fingerprint() -> None:
    """The futures block is excluded while unset; existing fingerprints do not move."""
    policy = compiled_default_risk_policy()
    assert "futures" not in policy.model_dump_json()
    with_block = _policy(live_spot_collateral_reserve_quote="10")
    assert risk_policy_fingerprint(with_block) != risk_policy_fingerprint(policy)
    with pytest.raises(ValueError, match="peg_haircut"):
        FuturesRiskPolicy(peg_haircut="0.99")
    with pytest.raises(ValueError, match="greater than 0"):
        FuturesRiskPolicy(live_spot_collateral_reserve_quote="0")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("latest", "futures", "effect"),
    [
        ("idle", {}, "none"),
        ("absent", {}, "none"),
        ("in_use", {}, "live_spot_entries_denied"),
        ("in_use", {"live_spot_collateral_reserve_quote": "90"}, "reserve_short_entries_denied"),
        ("in_use", {"live_spot_collateral_reserve_quote": "94"}, "reserve_withheld"),
    ],
)
async def test_risk_report_block_matches_the_gate(
    latest: str, futures: dict[str, str], effect: str
) -> None:
    """The operator risk report's collateral block states the gate's effect."""
    stored = {"idle": _live_account_idle(), "absent": None}.get(latest)
    store = _Store(await _observe_in_use() if latest == "in_use" else stored)
    payload = await futures_collateral_payload(store, _policy(**futures), now=_NOW)
    assert payload.effect == effect
    assert payload.state == latest
    assert "USDC" in payload.collateral_note


async def _observe_in_use() -> FuturesAccountObservation:
    """The in-use fixture inside a running loop."""
    return await observe_futures_account(
        CoinbaseCfmAccount(_transport()), _NOW - timedelta(seconds=30)
    )
