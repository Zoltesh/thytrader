"""Validation and fingerprint stability for risk-policy documents."""

from uuid import UUID

from pydantic import ValidationError
import pytest

from thytrader.risk.models import (
    COMPILED_POLICY_ID,
    CapitalAllocation,
    RiskPolicyDefinition,
    RiskPolicySource,
    RiskPolicyWrite,
    RiskReasonCode,
    canonical_risk_policy_bytes,
    compiled_default_active_policy,
    compiled_default_risk_policy,
    definition_from_stored_json,
    pauses_risk_increasing,
    risk_policy_fingerprint,
)


def test_compiled_default_is_the_conservative_multi_asset_envelope() -> None:
    """The compiled default must permit eight slots per mode and a 100000 paper book."""
    definition = compiled_default_risk_policy()
    assert definition.schema_version == "thytrader-risk-policy-v1"
    assert definition.policy_id == COMPILED_POLICY_ID
    assert definition.product_allowlist == ()
    assert definition.allocations == ()
    assert definition.max_concurrent_running_deployments == 8
    assert definition.max_concurrent_open_positions == 8
    assert definition.max_portfolio_exposure_fraction == "1"
    assert definition.per_product_max_exposure_fraction == "1"
    assert definition.paper_capital_quote == "100000"
    assert definition.daily_loss_limit_fraction == "1"
    assert definition.max_strategy_drawdown_fraction == "1"
    assert definition.max_entry_orders_per_minute == 60
    assert definition.max_cancellations_per_minute == 60
    assert definition.reference_price_collar_fraction == "0.5"
    assert definition.allow_intra_strategy_pyramiding is False
    active = compiled_default_active_policy()
    assert active.source is RiskPolicySource.COMPILED_DEFAULT
    assert active.policy_fingerprint == risk_policy_fingerprint(definition)
    assert active.policy_fingerprint.startswith("sha256:")
    assert definition.quote_currency == "USDC"


def test_fingerprint_is_stable_under_key_reordering() -> None:
    """Canonical identity must not depend on Python dict insertion order."""
    first = compiled_default_risk_policy()
    second = RiskPolicyDefinition.model_validate(first.model_dump(mode="python"))
    assert risk_policy_fingerprint(first) == risk_policy_fingerprint(second)


def test_allowlist_rejects_duplicates_and_non_usd_spot() -> None:
    """Allowlist entries must be unique BASE-USD product ids."""
    payload = compiled_default_risk_policy().model_dump(mode="python")
    with pytest.raises(ValidationError):
        RiskPolicyDefinition.model_validate(
            {**payload, "product_allowlist": ("BTC-USD", "BTC-USD")}
        )
    with pytest.raises(ValidationError):
        RiskPolicyDefinition.model_validate({**payload, "product_allowlist": ("BTC-EUR",)})


def test_allocations_must_fit_the_paper_book() -> None:
    """Reserved quote cannot exceed paper_capital_quote or repeat a strategy."""
    strategy = UUID("01978a3e-5f2c-7d10-b3a4-0000000000bb")
    other = UUID("01978a3e-5f2c-7d10-b3a4-0000000000cc")
    payload = compiled_default_risk_policy().model_dump(mode="python")
    with pytest.raises(ValidationError):
        RiskPolicyDefinition.model_validate(
            {
                **payload,
                "allocations": (
                    CapitalAllocation(strategy_id=strategy, allocated_quote="60000"),
                    CapitalAllocation(strategy_id=other, allocated_quote="60000"),
                ),
            }
        )
    with pytest.raises(ValidationError):
        RiskPolicyDefinition.model_validate(
            {
                **payload,
                "allocations": (
                    CapitalAllocation(strategy_id=strategy, allocated_quote="1000"),
                    CapitalAllocation(strategy_id=strategy, allocated_quote="2000"),
                ),
            }
        )


def test_only_daily_loss_and_drawdown_pause_risk_increasing() -> None:
    """Rate and collar denies must not share the pause-on-breaker reason set."""
    assert pauses_risk_increasing(RiskReasonCode.DAILY_LOSS_LIMIT) is True
    assert pauses_risk_increasing(RiskReasonCode.STRATEGY_DRAWDOWN_LIMIT) is True
    assert pauses_risk_increasing(RiskReasonCode.ORDER_RATE_LIMIT) is False
    assert pauses_risk_increasing(RiskReasonCode.REFERENCE_PRICE_COLLAR) is False
    assert pauses_risk_increasing(RiskReasonCode.BREAKER_MARK_MISSING) is False


def test_absolute_monetary_caps_are_optional_and_must_be_positive_when_set() -> None:
    """Unset absolute caps stay None; a zero or negative cap is rejected (F25)."""
    default = compiled_default_risk_policy()
    assert default.max_daily_loss_quote is None
    assert default.max_portfolio_exposure_quote is None
    payload = default.model_dump(mode="python")
    with pytest.raises(ValidationError):
        RiskPolicyDefinition.model_validate({**payload, "max_daily_loss_quote": "0"})
    with pytest.raises(ValidationError):
        RiskPolicyDefinition.model_validate({**payload, "max_portfolio_exposure_quote": "-5"})
    with_cap = RiskPolicyDefinition.model_validate({**payload, "max_daily_loss_quote": "500"})
    assert with_cap.max_daily_loss_quote == "500"


def test_venue_action_budget_is_optional_and_bounded_when_set() -> None:
    """The combined venue-request budget stays unset by default (F35)."""
    default = compiled_default_risk_policy()
    assert default.max_venue_order_actions_per_minute is None
    payload = default.model_dump(mode="python")
    with pytest.raises(ValidationError):
        RiskPolicyDefinition.model_validate({**payload, "max_venue_order_actions_per_minute": 0})
    with_budget = RiskPolicyDefinition.model_validate(
        {**payload, "max_venue_order_actions_per_minute": 120}
    )
    assert with_budget.max_venue_order_actions_per_minute == 120
    assert b"max_venue_order_actions_per_minute" not in canonical_risk_policy_bytes(default)
    assert b"max_venue_order_actions_per_minute" in canonical_risk_policy_bytes(with_budget)


def test_omitted_absolute_caps_preserve_compiled_fingerprint() -> None:
    """Unset absolute caps must not change the canonical policy identity (F25)."""
    default = compiled_default_risk_policy()
    explicit_none = default.model_copy(
        update={"max_daily_loss_quote": None, "max_portfolio_exposure_quote": None}
    )
    with_cap = default.model_copy(update={"max_daily_loss_quote": "500"})
    assert risk_policy_fingerprint(default) == risk_policy_fingerprint(explicit_none)
    assert risk_policy_fingerprint(with_cap) != risk_policy_fingerprint(default)
    assert b"max_daily_loss_quote" not in canonical_risk_policy_bytes(default)
    assert b"max_daily_loss_quote" in canonical_risk_policy_bytes(with_cap)


def test_omitted_pyramiding_flag_preserves_compiled_fingerprint() -> None:
    """False allow_intra_strategy_pyramiding must stay omitted from canonical policy JSON."""
    default = compiled_default_risk_policy()
    explicit_false = default.model_copy(update={"allow_intra_strategy_pyramiding": False})
    enabled = default.model_copy(update={"allow_intra_strategy_pyramiding": True})
    assert risk_policy_fingerprint(default) == risk_policy_fingerprint(explicit_false)
    assert risk_policy_fingerprint(enabled) != risk_policy_fingerprint(default)
    assert b"allow_intra_strategy_pyramiding" not in canonical_risk_policy_bytes(default)
    assert b"allow_intra_strategy_pyramiding" in canonical_risk_policy_bytes(enabled)


def test_count_limits_allow_large_fleets_up_to_their_bounds() -> None:
    """Running and open-position counts accept 128; the allowlist accepts 256 products."""
    payload = compiled_default_risk_policy().model_dump(mode="python")
    widened = RiskPolicyDefinition.model_validate(
        {
            **payload,
            "max_concurrent_running_deployments": 128,
            "max_concurrent_open_positions": 128,
            "product_allowlist": tuple(f"C{index}-USD" for index in range(256)),
        }
    )
    assert widened.max_concurrent_running_deployments == 128
    for field in ("max_concurrent_running_deployments", "max_concurrent_open_positions"):
        with pytest.raises(ValidationError):
            RiskPolicyDefinition.model_validate({**payload, field: 129})
    with pytest.raises(ValidationError):
        RiskPolicyDefinition.model_validate(
            {**payload, "product_allowlist": tuple(f"C{index}-USD" for index in range(257))}
        )


_COMPILED_FINGERPRINT = "sha256:7e7f02402a5c77932e147a0004cd18ad01a6eed0a8facb0381286e848515e4a6"
_CONFIGURED_FINGERPRINT = "sha256:0bba6e42ae327b87504c385f5d7acc1eda4cf180cccbcceeaccb87edf4be5360"


def test_policies_without_clustering_fields_keep_their_fingerprints() -> None:
    """ADR 0125 fields are omitted when unset: pre-0125 identities are byte-for-byte unchanged.

    Both values were computed with the risk models from before the clustering fields existed.
    """
    default = compiled_default_risk_policy()
    configured = RiskPolicyDefinition.model_validate(
        {
            **default.model_dump(mode="python"),
            "max_daily_loss_quote": "25",
            "max_portfolio_exposure_quote": "490",
            "max_order_quantity": "1",
            "min_available_quote_reserve": "5",
            "max_venue_order_actions_per_minute": 90,
            "allow_intra_strategy_pyramiding": True,
        }
    )
    assert risk_policy_fingerprint(default) == _COMPILED_FINGERPRINT
    assert risk_policy_fingerprint(configured) == _CONFIGURED_FINGERPRINT
    assert b"fleet" not in canonical_risk_policy_bytes(configured)
    assert b"btc_beta" not in canonical_risk_policy_bytes(configured)


def test_clustering_fields_change_the_fingerprint_when_set() -> None:
    """A published clustering cap is part of the policy identity and round-trips."""
    payload = compiled_default_risk_policy().model_dump(mode="python")
    clustered = RiskPolicyDefinition.model_validate(
        {**payload, "max_fleet_entries_per_window": 4, "fleet_entry_window_minutes": 120}
    )
    canonical = canonical_risk_policy_bytes(clustered)
    assert risk_policy_fingerprint(clustered) != _COMPILED_FINGERPRINT
    assert b'"max_fleet_entries_per_window":4' in canonical
    assert b'"fleet_entry_window_minutes":120' in canonical
    assert definition_from_stored_json(canonical.decode("utf-8")) == clustered


@pytest.mark.parametrize(
    "fields",
    [{"max_fleet_entries_per_window": 4}, {"fleet_entry_window_minutes": 120}],
)
def test_clustering_count_and_window_must_be_set_together(fields: dict[str, int]) -> None:
    """A count without a window, or a window without a count, is rejected on both models."""
    payload = compiled_default_risk_policy().model_dump(mode="python")
    with pytest.raises(ValidationError, match="must be set together"):
        RiskPolicyDefinition.model_validate({**payload, **fields})
    write = {
        "max_concurrent_running_deployments": 8,
        "max_concurrent_open_positions": 8,
        "max_portfolio_exposure_fraction": "1",
        "per_product_max_exposure_fraction": "1",
        "paper_capital_quote": "1000",
    }
    with pytest.raises(ValidationError, match="must be set together"):
        RiskPolicyWrite.model_validate({**write, **fields})


@pytest.mark.parametrize(
    ("field", "accepted", "rejected"),
    [
        ("max_fleet_entries_per_window", (1, 128), (0, 129)),
        ("fleet_entry_window_minutes", (1, 1440), (0, 1441)),
    ],
)
def test_clustering_bounds(
    field: str, accepted: tuple[int, int], rejected: tuple[int, int]
) -> None:
    """The count accepts 1-128 and the window 1-1440 minutes."""
    payload = {
        **compiled_default_risk_policy().model_dump(mode="python"),
        "max_fleet_entries_per_window": 4,
        "fleet_entry_window_minutes": 120,
    }
    for value in accepted:
        validated = RiskPolicyDefinition.model_validate({**payload, field: value})
        assert validated.model_dump()[field] == value
    for value in rejected:
        with pytest.raises(ValidationError):
            RiskPolicyDefinition.model_validate({**payload, field: value})


def test_cluster_limit_does_not_pause_books() -> None:
    """A clustering denial skips one entry; it is not a breaker."""
    assert pauses_risk_increasing(RiskReasonCode.FLEET_ENTRY_CLUSTER_LIMIT) is False


def test_beta_denials_do_not_pause_books() -> None:
    """β-cap and missing-β denials skip one entry; they are not breakers."""
    assert pauses_risk_increasing(RiskReasonCode.BTC_BETA_EXPOSURE_EXCEEDED) is False
    assert pauses_risk_increasing(RiskReasonCode.BTC_BETA_UNAVAILABLE) is False


def test_beta_fields_round_trip_and_change_the_fingerprint() -> None:
    """Set β fields are part of the identity and survive canonical storage."""
    payload = compiled_default_risk_policy().model_dump(mode="python")
    capped = RiskPolicyDefinition.model_validate(
        {
            **payload,
            "max_btc_beta_exposure_fraction": "0.6",
            "max_btc_beta_exposure_quote": "300",
        }
    )
    canonical = canonical_risk_policy_bytes(capped)
    assert risk_policy_fingerprint(capped) != _COMPILED_FINGERPRINT
    assert b'"max_btc_beta_exposure_fraction":"0.6"' in canonical
    assert b'"max_btc_beta_exposure_quote":"300"' in canonical
    assert definition_from_stored_json(canonical.decode("utf-8")) == capped


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_btc_beta_exposure_fraction", "0"),
        ("max_btc_beta_exposure_fraction", "1.01"),
        ("max_btc_beta_exposure_fraction", "-0.1"),
        ("max_btc_beta_exposure_quote", "0"),
        ("max_btc_beta_exposure_quote", "-5"),
    ],
)
def test_beta_fields_reject_out_of_range_values(field: str, value: str) -> None:
    """The fraction must be in (0, 1] and the quote cap positive."""
    payload = compiled_default_risk_policy().model_dump(mode="python")
    with pytest.raises(ValidationError):
        RiskPolicyDefinition.model_validate({**payload, field: value})


def test_beta_fields_accept_either_alone() -> None:
    """The fraction and the live absolute cap are independent."""
    payload = compiled_default_risk_policy().model_dump(mode="python")
    fraction_only = RiskPolicyDefinition.model_validate(
        {**payload, "max_btc_beta_exposure_fraction": "1"}
    )
    quote_only = RiskPolicyDefinition.model_validate(
        {**payload, "max_btc_beta_exposure_quote": "250"}
    )
    assert fraction_only.max_btc_beta_exposure_quote is None
    assert quote_only.max_btc_beta_exposure_fraction is None
