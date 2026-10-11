"""Settlement-scope and loaded-economics regressions for live futures admission."""

from dataclasses import replace
from decimal import Decimal, Overflow, localcontext

import pytest

from tests.risk.test_futures_gate_caps import _PERP, _book, _observation, _state
from tests.risk.test_futures_live import book, policy, venue
from thytrader.risk.breakers import evaluate_circuit_breakers
from thytrader.risk.futures_policy import FuturesRiskPolicy
from thytrader.risk.gate import evaluate_new_entry
from thytrader.risk.gate_common import ProposedEntry
from thytrader.risk.models import RiskPolicyDefinition, RiskReasonCode, RiskVerdict
from thytrader.trading.futures_book import futures_book_equity, futures_book_scope
from thytrader.trading.models import DeploymentMode, DeploymentSnapshot


def _verdict(
    snapshot: DeploymentSnapshot,
    definition: RiskPolicyDefinition,
    others: tuple[DeploymentSnapshot, ...] = (),
) -> RiskVerdict:
    """Reach the public entry gate with validated policy and synthetic venue evidence."""
    definition = RiskPolicyDefinition.model_validate(definition.model_dump())
    with futures_book_scope(_state(snapshot)):
        return evaluate_new_entry(
            definition,
            mode=snapshot.deployment.mode,
            proposed=ProposedEntry(
                product_id=_PERP,
                strategy_id=snapshot.deployment.strategy_id,
                quantity=Decimal("0.01"),
                notional=Decimal(1),
            ),
            snapshots=(snapshot, *others),
            observation=_observation(),
            futures_venue=venue(),
        )


@pytest.mark.parametrize("spot_quote", ["USDC", "USD"])
@pytest.mark.parametrize("spot_ceiling", ["0.01", "5", "10", "100"])
def test_spot_loss_ceiling_does_not_become_futures_usd_ceiling(
    spot_quote: str, spot_ceiling: str
) -> None:
    """Even USD spot and CFM USD have separate absolute loss limits."""
    snapshot = book()
    snapshot = replace(snapshot, deployment=replace(snapshot.deployment, cash=Decimal(-10)))
    definition = policy().model_copy(update={"quote_currency": spot_quote})
    assert _verdict(snapshot, definition).reason_code is RiskReasonCode.ALLOWED
    capped_spot = definition.model_copy(update={"max_daily_loss_quote": spot_ceiling})
    assert _verdict(snapshot, capped_spot).reason_code is RiskReasonCode.ALLOWED


@pytest.mark.parametrize("allocation", [None, "0", "-1", "NaN", "sNaN", "Infinity", "-Infinity"])
def test_invalid_loaded_live_allocation_returns_controlled_denial(allocation: str | None) -> None:
    """Pinned finite performance capital cannot mask invalid margin authority."""
    snapshot = book()
    snapshot = replace(
        snapshot,
        deployment=replace(
            snapshot.deployment,
            allocated_capital=None if allocation is None else Decimal(allocation),
            performance_capital_quote=Decimal(1000),
            cash=Decimal(100),
        ),
    )
    assert _verdict(snapshot, policy()).reason_code is RiskReasonCode.BREAKER_MARK_MISSING
    assert futures_book_equity(snapshot, {}) is None


@pytest.mark.parametrize("cash", ["NaN", "sNaN", "Infinity", "-Infinity"])
def test_nonfinite_ledger_cash_is_unknown(cash: str) -> None:
    """Loaded nonfinite ledger economics deny rather than escaping as Decimal errors."""
    snapshot = book()
    snapshot = replace(
        snapshot,
        deployment=replace(
            snapshot.deployment,
            cash=Decimal(cash),
            performance_capital_quote=Decimal(1000),
        ),
    )
    assert _verdict(snapshot, policy()).reason_code is RiskReasonCode.BREAKER_MARK_MISSING
    assert futures_book_equity(snapshot, {}) is None


@pytest.mark.parametrize("trap_overflow", [True, False])
def test_computed_equity_overflow_is_unknown(trap_overflow: bool) -> None:
    """Finite inputs cannot certify a nonfinite sum, regardless of Decimal trap settings."""
    snapshot = book()
    snapshot = replace(
        snapshot,
        deployment=replace(
            snapshot.deployment,
            allocated_capital=Decimal(9000),
            cash=Decimal(1000),
            performance_capital_quote=Decimal(1000),
        ),
    )
    with localcontext() as context:
        context.Emax = 3
        context.traps[Overflow] = trap_overflow
        assert futures_book_equity(snapshot, {}) is None


@pytest.mark.parametrize("mode", list(DeploymentMode))
@pytest.mark.parametrize(
    ("fraction", "ceiling", "expected"),
    [
        ("0.002", None, RiskReasonCode.ALLOWED),
        ("0.001", None, RiskReasonCode.DAILY_LOSS_LIMIT),
        ("0.0005", "100", RiskReasonCode.DAILY_LOSS_LIMIT),
        ("0.002", "5", RiskReasonCode.DAILY_LOSS_LIMIT),
        ("0.002", "10", RiskReasonCode.DAILY_LOSS_LIMIT),
        ("0.002", "10.01", RiskReasonCode.ALLOWED),
    ],
)
def test_futures_own_loss_limits_bind_in_both_modes(
    mode: DeploymentMode, fraction: str, ceiling: str | None, expected: RiskReasonCode
) -> None:
    """The tighter futures cap binds inclusively, never the deliberately tiny spot ceiling."""
    snapshot = book() if mode is DeploymentMode.LIVE else _book(_PERP)
    snapshot = replace(
        snapshot, deployment=replace(snapshot.deployment, cash=snapshot.deployment.cash - 10)
    )
    base = policy()
    assert base.futures is not None
    futures = FuturesRiskPolicy.model_validate(
        base.futures.model_dump()
        | {
            "paper_capital_usd": "10000",
            "daily_loss_limit_fraction": fraction,
            "max_daily_loss_usd": ceiling,
        }
    )
    definition = base.model_copy(update={"futures": futures, "max_daily_loss_quote": "0.01"})
    assert _verdict(snapshot, definition).reason_code is expected


@pytest.mark.parametrize("mode", list(DeploymentMode))
@pytest.mark.parametrize("quote", ["USD", "USDC"])
@pytest.mark.parametrize("spot_ceiling", [None, "5"])
def test_spot_absolute_loss_limit_stays_live_only(
    mode: DeploymentMode, quote: str, spot_ceiling: str | None
) -> None:
    """A futures ceiling cannot affect spot; the spot ceiling still binds only live."""
    snapshot = _book(f"ETH-{quote}", cash="9990")
    snapshot = replace(snapshot, deployment=replace(snapshot.deployment, mode=mode))
    definition = RiskPolicyDefinition.model_validate(
        policy().model_dump()
        | {
            "quote_currency": quote,
            "max_daily_loss_quote": spot_ceiling,
            "futures": FuturesRiskPolicy(max_daily_loss_usd="1").model_dump(),
        }
    )
    verdict = evaluate_circuit_breakers(
        definition,
        mode=mode,
        proposed_product_id=snapshot.deployment.product_id,
        proposed_strategy_id=None,
        snapshots=(snapshot,),
        observation=_observation(),
        capital=Decimal(10000),
    )
    if mode is DeploymentMode.LIVE and spot_ceiling is not None:
        assert verdict is not None
        assert verdict.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT
    else:
        assert verdict is None


@pytest.mark.parametrize("quote", ["USD", "USDC", "USDT"])
@pytest.mark.parametrize("other_mode", list(DeploymentMode))
def test_spot_latch_links_without_importing_spot_loss_ceiling(
    quote: str, other_mode: DeploymentMode
) -> None:
    """Only same-mode linked latches deny CFM; spot losses and ceilings stay separate."""
    snapshot = book()
    spot = _book(f"ETH-{quote}", cash="0")
    spot = replace(spot, deployment=replace(spot.deployment, mode=other_mode))
    definition = policy().model_copy(update={"max_daily_loss_quote": "0.01"})
    assert _verdict(snapshot, definition, (spot,)).reason_code is RiskReasonCode.ALLOWED
    latched = replace(spot, deployment=replace(spot.deployment, daily_loss_latched=True))
    expected = (
        RiskReasonCode.SHARED_COLLATERAL_BREAKER
        if other_mode is DeploymentMode.LIVE and quote != "USDT"
        else RiskReasonCode.ALLOWED
    )
    assert _verdict(snapshot, definition, (latched,)).reason_code is expected


@pytest.mark.parametrize("cash", ["-1001", "-1000", "-10", "0", "100"])
def test_valid_live_equity_keeps_exact_economics(cash: str) -> None:
    """Finite exhausted equity remains evidence, but cannot admit more margin."""
    snapshot = book()
    snapshot = replace(snapshot, deployment=replace(snapshot.deployment, cash=Decimal(cash)))
    assert futures_book_equity(snapshot, {}) == Decimal(1000) + Decimal(cash)
    if Decimal(cash) <= -1000:
        assert _verdict(snapshot, policy()).reason_code is RiskReasonCode.BREAKER_MARK_MISSING


@pytest.mark.parametrize("allocation", [None, "NaN", "sNaN", "Infinity", "-1", "1000"])
def test_paper_equity_does_not_use_live_allocation(allocation: str | None) -> None:
    """Paper continues to value only its ledger; live allocation is not paper capital."""
    snapshot = _book(_PERP, cash="990")
    snapshot = replace(
        snapshot,
        deployment=replace(
            snapshot.deployment,
            allocated_capital=None if allocation is None else Decimal(allocation),
        ),
    )
    assert futures_book_equity(snapshot, {}) == Decimal(990)


@pytest.mark.parametrize("mark", [None, "NaN", "sNaN", "Infinity", "-Infinity"])
def test_unusable_open_book_mark_returns_unknown_equity(mark: str | None) -> None:
    """Missing or malformed ledger marks never become margin authority or raw errors."""
    snapshot = _book(_PERP, quantity="0.01")
    snapshot = replace(
        snapshot,
        deployment=replace(
            book().deployment, id=snapshot.deployment.id, performance_capital_quote=Decimal(1000)
        ),
    )
    marks = {} if mark is None else {_PERP: Decimal(mark)}
    assert futures_book_equity(snapshot, marks) is None
