"""Portfolio validation and mutation planning (ADR 0088)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
import pytest

from thytrader.portfolios.models import (
    ManagerPermissions,
    MutationContext,
    PortfolioAggregate,
    PortfolioCreateRequest,
    PortfolioLimits,
    PortfolioRevisionConflictError,
    PortfolioSleeveExistsError,
    PortfolioUpdateRequest,
    PortfolioValidationError,
    SetWeightsRequest,
    SleeveAddRequest,
    SleeveStrategy,
    SleeveUpdateRequest,
    SleeveView,
    canonical_decimal_text,
    document_product_ids,
    sleeve_issues,
)
from thytrader.portfolios.rules import (
    allocation_summary,
    percent_text,
    plan_add_sleeve,
    plan_create,
    plan_remove_sleeve,
    plan_set_weights,
    plan_update,
    plan_update_sleeve,
    quote_text,
)

_NOW = datetime(2026, 10, 2, 12, tzinfo=UTC)
_CONTEXT = MutationContext(actor="operator", channel="api", occurred_at=_NOW)
_PORTFOLIO_ID = UUID("01a0f000-0000-7000-8000-000000000001")


def _strategy(
    number: int, product: str = "BTC-USDC", *, covered: tuple[str, ...] = ()
) -> SleeveStrategy:
    """One strategy's sleeve facts."""
    return SleeveStrategy(
        strategy_id=UUID(f"01a0f000-0000-7000-8000-0000000001{number:02d}"),
        name=f"Strategy {number}",
        product_id=product,
        covered_product_ids=covered or (product,),
        timeframe="1h",
        valid=True,
        current_fingerprint="sha256:" + "a" * 64,
    )


def _created(reserve: str = "0.1") -> PortfolioAggregate:
    """A fresh USDC portfolio with 1000 capital."""
    plan = plan_create(
        PortfolioCreateRequest(
            name=" Core ",
            mode="paper",
            capital_quote="1000",
            cash_reserve_fraction=reserve,
        ),
        portfolio_id=_PORTFOLIO_ID,
        context=_CONTEXT,
    )
    return PortfolioAggregate(portfolio=plan.portfolio, sleeves=())


def _with_sleeve(
    current: PortfolioAggregate, strategy: SleeveStrategy, weight: str, number: int
) -> PortfolioAggregate:
    """Apply one add-sleeve plan and return the next aggregate."""
    plan = plan_add_sleeve(
        current,
        strategy,
        SleeveAddRequest(
            revision=current.portfolio.revision,
            strategy_id=strategy.strategy_id,
            weight_fraction=weight,
        ),
        sleeve_id=UUID(f"01a0f000-0000-7000-8000-0000000002{number:02d}"),
        context=_CONTEXT,
    )
    views = {view.sleeve.strategy_id: view.strategy for view in current.sleeves}
    views[strategy.strategy_id] = strategy
    return PortfolioAggregate(
        portfolio=plan.portfolio,
        sleeves=tuple(
            SleeveView(sleeve=item, strategy=views[item.strategy_id]) for item in plan.sleeves
        ),
    )


def test_decimal_fields_are_canonical_and_bounded() -> None:
    """Weights keep four places, quote amounts eight; out-of-range values are refused."""
    assert canonical_decimal_text("0.5000", places=4, label="x") == "0.5"
    with pytest.raises(ValueError, match="plain decimal string"):
        canonical_decimal_text("00", places=4, label="x")
    with pytest.raises(ValueError, match="at most 4 decimal places"):
        canonical_decimal_text("0.33333", places=4, label="x")
    with pytest.raises(ValidationError):
        SleeveAddRequest(revision=1, strategy_id=_strategy(1).strategy_id, weight_fraction="0")
    with pytest.raises(ValidationError):
        SleeveAddRequest(revision=1, strategy_id=_strategy(1).strategy_id, weight_fraction="1.01")
    with pytest.raises(ValidationError):
        PortfolioCreateRequest(name="x", mode="paper", capital_quote="1.123456789")
    with pytest.raises(ValidationError):
        PortfolioCreateRequest(
            name="x", mode="paper", capital_quote="10", cash_reserve_fraction="1"
        )
    with pytest.raises(ValidationError):
        PortfolioLimits(max_drawdown_fraction="1")
    assert PortfolioCreateRequest(name=" Lab ", mode="live", capital_quote="10.50").name == "Lab"


def test_manager_permissions_can_never_grant_order_authority() -> None:
    """An unknown may_* permission is refused with the manager's fixed boundary."""
    with pytest.raises(ValidationError, match="never places orders"):
        ManagerPermissions.model_validate({"may_place_orders": True})
    with pytest.raises(ValidationError, match="fixed when a portfolio is created"):
        PortfolioUpdateRequest.model_validate({"revision": 1, "mode": "live"})
    with pytest.raises(ValidationError, match="Name at least one"):
        PortfolioUpdateRequest(revision=1)


def test_create_journals_the_starting_settings() -> None:
    """Creation starts at revision 1 with one created entry."""
    plan = plan_create(
        PortfolioCreateRequest(
            name="Core", mode="live", capital_quote="300", cash_reserve_fraction="0.17"
        ),
        portfolio_id=_PORTFOLIO_ID,
        context=_CONTEXT,
    )
    assert plan.portfolio.revision == 1
    assert [entry.kind for entry in plan.journal] == ["created"]
    entry = plan.journal[0]
    assert entry.summary == "Created live portfolio “Core” with 300 USDC and a 17% cash reserve."
    assert (entry.actor, entry.channel, entry.revision) == ("operator", "api", 1)


def test_weights_plus_reserve_never_exceed_one_exactly() -> None:
    """0.3333 + 0.3333 + 0.3334 fits with no reserve; one more 0.0001 does not."""
    current = _with_sleeve(_created("0"), _strategy(1), "0.3333", 1)
    current = _with_sleeve(current, _strategy(2, "ETH-USDC"), "0.3333", 2)
    current = _with_sleeve(current, _strategy(3, "SOL-USDC"), "0.3334", 3)
    assert allocation_summary(current).unallocated_fraction == "0"
    with pytest.raises(PortfolioValidationError) as raised:
        _with_sleeve(current, _strategy(4, "ADA-USDC"), "0.0001", 4)
    assert raised.value.code == "portfolio_allocation_exceeded"
    with pytest.raises(PortfolioValidationError, match="must not exceed 100%"):
        plan_update(
            current,
            PortfolioUpdateRequest(
                revision=current.portfolio.revision, cash_reserve_fraction="0.0001"
            ),
            context=_CONTEXT,
        )


def test_add_sleeve_refuses_duplicates_other_quotes_and_stale_revisions() -> None:
    """One sleeve per strategy, the portfolio's quote only, and a current revision."""
    current = _with_sleeve(_created(), _strategy(1), "0.5", 1)
    with pytest.raises(PortfolioSleeveExistsError):
        _with_sleeve(current, _strategy(1), "0.1", 2)
    with pytest.raises(PortfolioValidationError) as raised:
        _with_sleeve(current, _strategy(5, "BTC-USD"), "0.1", 3)
    assert raised.value.code == "portfolio_sleeve_quote_mismatch"
    stale = SleeveAddRequest(
        revision=1, strategy_id=_strategy(6).strategy_id, weight_fraction="0.1"
    )
    with pytest.raises(PortfolioRevisionConflictError) as conflict:
        plan_add_sleeve(current, _strategy(6), stale, sleeve_id=UUID(int=9), context=_CONTEXT)
    assert conflict.value.current_revision == 2


def test_set_weights_requires_every_sleeve_and_journals_each_change() -> None:
    """A partial weight set is refused; a full one journals before and after values."""
    current = _with_sleeve(_created(), _strategy(1), "0.5", 1)
    current = _with_sleeve(current, _strategy(2, "ETH-USDC"), "0.3", 2)
    first, second = (view.sleeve.sleeve_id for view in current.sleeves)
    with pytest.raises(PortfolioValidationError) as raised:
        plan_set_weights(
            current,
            SetWeightsRequest.model_validate(
                {"revision": 3, "weights": [{"sleeve_id": str(first), "weight_fraction": "0.4"}]}
            ),
            context=_CONTEXT,
        )
    assert raised.value.code == "portfolio_weights_incomplete"
    unchanged = SetWeightsRequest.model_validate(
        {
            "revision": 3,
            "weights": [
                {"sleeve_id": str(first), "weight_fraction": "0.5"},
                {"sleeve_id": str(second), "weight_fraction": "0.3"},
            ],
        }
    )
    assert plan_set_weights(current, unchanged, context=_CONTEXT) is None
    plan = plan_set_weights(
        current,
        SetWeightsRequest.model_validate(
            {
                "revision": 3,
                "weights": [
                    {"sleeve_id": str(first), "weight_fraction": "0.4"},
                    {"sleeve_id": str(second), "weight_fraction": "0.4"},
                ],
                "cash_reserve_fraction": "0.2",
            }
        ),
        context=_CONTEXT,
    )
    assert plan is not None
    assert plan.portfolio.revision == 4
    assert plan.portfolio.cash_reserve_fraction == "0.2"
    assert plan.journal[0].summary == (
        "Changed weights: Strategy 1 50% → 40%; Strategy 2 30% → 40%; cash reserve 10% → 20%."
    )


def test_sleeve_update_and_removal_journal_their_reason() -> None:
    """Weight edits are weights_changed, notes sleeve_updated; deletions say why."""
    current = _with_sleeve(_created(), _strategy(1), "0.5", 1)
    sleeve_id = current.sleeves[0].sleeve.sleeve_id
    plan = plan_update_sleeve(
        current,
        sleeve_id,
        SleeveUpdateRequest(revision=2, weight_fraction="0.6", note="core trend"),
        context=_CONTEXT,
    )
    assert plan is not None
    assert [entry.kind for entry in plan.journal] == ["weights_changed", "sleeve_updated"]
    removed = plan_remove_sleeve(
        current,
        sleeve_id,
        expected_revision=None,
        context=MutationContext(actor="system", channel="system", occurred_at=_NOW),
        strategy_deleted=True,
    )
    entry = removed.journal[0]
    assert removed.sleeves == ()
    assert (entry.kind, entry.actor, entry.detail.reason) == (
        "sleeve_removed",
        "system",
        "strategy_deleted",
    )
    assert entry.summary == "Removed sleeve “Strategy 1” (50%) because its strategy was deleted."


def test_allocation_counts_multi_product_sleeves_toward_each_asset() -> None:
    """A two-product sleeve's weight counts toward both assets; the largest is checked."""
    current = _with_sleeve(_created(), _strategy(1, covered=("BTC-USDC", "ETH-USDC")), "0.4", 1)
    current = _with_sleeve(current, _strategy(2, "ETH-USDC"), "0.3", 2)
    summary = allocation_summary(current)
    assert [(item.asset, item.weight_fraction) for item in summary.assets] == [
        ("ETH", "0.7"),
        ("BTC", "0.4"),
    ]
    assert summary.largest_asset_within_limit is True
    limited = plan_update(
        current,
        PortfolioUpdateRequest(revision=3, limits=PortfolioLimits(max_per_asset_fraction="0.6")),
        context=_CONTEXT,
    )
    assert limited is not None
    assert limited.journal[0].summary == "Changed limits: max per asset 100% → 60%."


def test_sleeve_issues_and_document_products() -> None:
    """Drifted strategies are flagged; invalid documents still name their products."""
    view = SleeveView(
        sleeve=_with_sleeve(_created(), _strategy(1), "0.5", 1).sleeves[0].sleeve,
        strategy=SleeveStrategy(
            strategy_id=_strategy(1).strategy_id,
            name="Drifted",
            product_id="BTC-USD",
            covered_product_ids=("BTC-USD",),
            timeframe="1h",
            valid=False,
            current_fingerprint=None,
        ),
    )
    assert sleeve_issues(view, "USDC") == ("quote_currency_mismatch", "strategy_invalid")
    document = {"additional_instruments": [{"product_id": "ETH-USDC"}, {"product_id": "bad"}]}
    assert document_product_ids(document, "BTC-USDC") == ("BTC-USDC", "ETH-USDC")
    assert percent_text("0.3333") == "33.33%"
    assert quote_text("1234567.5", "USDC") == "1,234,567.5 USDC"
