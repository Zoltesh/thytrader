"""Immutable risk-policy documents and canonical fingerprints."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from thytrader.market_data.products import SPOT_PRODUCT_ID_PATTERN, SpotQuoteCurrency
from thytrader.strategies.models import (
    DecimalText,  # noqa: TC001 - Pydantic fields resolve this alias.
)

RISK_POLICY_SCHEMA_VERSION: Literal["thytrader-risk-policy-v1"] = "thytrader-risk-policy-v1"
COMPILED_POLICY_ID = UUID("01978a3e-5f2c-7d10-b3a4-0000000000aa")
_FINGERPRINT_PREFIX = "sha256:"
# These compiled fractions are a wide multi-asset *research* envelope, not an audited
# "safe" live number: no universal loss/drawdown percentage is a fact independent of
# the operator's capital, product, and tested strategy (see ADR 0063 and F25/C-4 in
# the 2026-09-16 audit). They stay permissive for paper; live instead requires an
# operator-published RiskPolicyDefinition — see RiskReasonCode.LIVE_REQUIRES_PUBLISHED_POLICY
# and evaluate_new_deployment. Operators set tighter fractions and/or the optional
# absolute quote caps below on the policy they publish for their own live posture.
DEFAULT_DAILY_LOSS_LIMIT_FRACTION = "1"
DEFAULT_MAX_STRATEGY_DRAWDOWN_FRACTION = "1"
DEFAULT_MAX_ENTRY_ORDERS_PER_MINUTE = 60
DEFAULT_MAX_CANCELLATIONS_PER_MINUTE = 60
DEFAULT_REFERENCE_PRICE_COLLAR_FRACTION = "0.5"
_LEGACY_BREAKER_KEYS: tuple[str, ...] = (
    "daily_loss_limit_fraction",
    "max_strategy_drawdown_fraction",
    "max_entry_orders_per_minute",
    "max_cancellations_per_minute",
    "reference_price_collar_fraction",
)


class RiskPolicySource(StrEnum):
    """Where the effective policy was loaded from."""

    COMPILED_DEFAULT = "compiled_default"
    PUBLISHED = "published"


class RiskDecision(StrEnum):
    """Allow or deny one deploy or entry under the active policy."""

    ALLOW = "allow"
    DENY = "deny"


class RiskReasonCode(StrEnum):
    """Stable codes for gate verdicts and operator findings."""

    ALLOWED = "ALLOWED"
    PRODUCT_NOT_ALLOWLISTED = "PRODUCT_NOT_ALLOWLISTED"
    MAX_RUNNING_DEPLOYMENTS = "MAX_RUNNING_DEPLOYMENTS"
    MAX_OPEN_POSITIONS = "MAX_OPEN_POSITIONS"
    PAPER_CAPITAL_EXCEEDED = "PAPER_CAPITAL_EXCEEDED"
    STRATEGY_NOT_ALLOCATED = "STRATEGY_NOT_ALLOCATED"
    ALLOCATION_EXCEEDED = "ALLOCATION_EXCEEDED"
    PORTFOLIO_EXPOSURE_EXCEEDED = "PORTFOLIO_EXPOSURE_EXCEEDED"
    PRODUCT_EXPOSURE_EXCEEDED = "PRODUCT_EXPOSURE_EXCEEDED"
    DISCRETIONARY_NOT_ALLOCATED = "DISCRETIONARY_NOT_ALLOCATED"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    STRATEGY_DRAWDOWN_LIMIT = "STRATEGY_DRAWDOWN_LIMIT"
    ORDER_RATE_LIMIT = "ORDER_RATE_LIMIT"
    CANCEL_RATE_LIMIT = "CANCEL_RATE_LIMIT"
    REFERENCE_PRICE_COLLAR = "REFERENCE_PRICE_COLLAR"
    REFERENCE_PRICE_UNAVAILABLE = "REFERENCE_PRICE_UNAVAILABLE"
    BREAKER_MARK_MISSING = "BREAKER_MARK_MISSING"
    PYRAMIDING_NOT_ALLOWED = "PYRAMIDING_NOT_ALLOWED"
    LIVE_REQUIRES_PUBLISHED_POLICY = "LIVE_REQUIRES_PUBLISHED_POLICY"
    VENUE_REQUEST_BUDGET_EXCEEDED = "VENUE_REQUEST_BUDGET_EXCEEDED"
    STALE_MARK = "STALE_MARK"
    PRODUCT_DISABLED = "PRODUCT_DISABLED"
    CONNECTION_UNHEALTHY = "CONNECTION_UNHEALTHY"
    VENUE_BALANCE_UNKNOWN = "VENUE_BALANCE_UNKNOWN"
    SIGNAL_STALE = "SIGNAL_STALE"
    ENTRIES_DISABLED = "ENTRIES_DISABLED"


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class CapitalAllocation(_FrozenModel):
    """Quote budget reserved for one published strategy identity."""

    strategy_id: UUID
    allocated_quote: DecimalText

    @field_validator("allocated_quote")
    @classmethod
    def require_positive_allocation(cls, value: str) -> str:
        """Reject zero or negative reserved capital."""
        if Decimal(value) <= 0:
            raise ValueError("allocated_quote must be greater than 0")
        return value


class RiskPolicyDefinition(_FrozenModel):
    """One immutable portfolio risk policy shared by paper and live."""

    schema_version: Literal["thytrader-risk-policy-v1"] = RISK_POLICY_SCHEMA_VERSION
    policy_id: UUID
    version: int = Field(ge=1)
    quote_currency: SpotQuoteCurrency = "USD"
    product_allowlist: tuple[str, ...] = Field(default=(), max_length=32)
    max_concurrent_running_deployments: int = Field(ge=1, le=32)
    max_concurrent_open_positions: int = Field(ge=1, le=32)
    max_portfolio_exposure_fraction: DecimalText
    per_product_max_exposure_fraction: DecimalText
    paper_capital_quote: DecimalText
    allocations: tuple[CapitalAllocation, ...] = ()
    daily_loss_limit_fraction: DecimalText = DEFAULT_DAILY_LOSS_LIMIT_FRACTION
    max_strategy_drawdown_fraction: DecimalText = DEFAULT_MAX_STRATEGY_DRAWDOWN_FRACTION
    max_entry_orders_per_minute: int = Field(
        default=DEFAULT_MAX_ENTRY_ORDERS_PER_MINUTE, ge=1, le=1000
    )
    max_cancellations_per_minute: int = Field(
        default=DEFAULT_MAX_CANCELLATIONS_PER_MINUTE, ge=1, le=1000
    )
    reference_price_collar_fraction: DecimalText = DEFAULT_REFERENCE_PRICE_COLLAR_FRACTION
    allow_intra_strategy_pyramiding: bool = Field(
        default=False, exclude_if=lambda value: value is False
    )
    # Optional absolute monetary ceilings alongside the fractional caps above. Unset
    # (None) by default: this module does not assert a universal safe quote amount.
    # When an operator sets one, breaker/exposure checks enforce the tighter of the
    # fraction-derived limit and this absolute cap (see risk/breakers.py, risk/gate.py).
    max_daily_loss_quote: DecimalText | None = Field(default=None, exclude_if=lambda v: v is None)
    max_portfolio_exposure_quote: DecimalText | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    # A combined per-minute budget across entry, cancel, and replacement requests,
    # i.e. every order-mutating REST call this policy's occupied books send toward
    # one venue (audit F35). Unset (None) by default: this module does not assert
    # a specific venue rate limit as a fact. Only new-entry admission is denied when
    # this budget is exhausted; protective/cancellation work is never gated here
    # because entry admission is the only caller of this check, so risk-reducing
    # activity keeps flowing even while this cap blocks new risk-increasing orders.
    max_venue_order_actions_per_minute: int | None = Field(
        default=None, ge=1, le=10000, exclude_if=lambda v: v is None
    )

    @field_validator("product_allowlist")
    @classmethod
    def require_unique_allowlist(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject duplicate or malformed product identifiers."""
        if len(value) != len(set(value)):
            raise ValueError("product_allowlist must be unique")
        return value

    @field_validator("max_daily_loss_quote", "max_portfolio_exposure_quote")
    @classmethod
    def require_positive_absolute_cap(cls, value: str | None) -> str | None:
        """Reject a zero or negative absolute monetary ceiling when one is set."""
        if value is not None and Decimal(value) <= 0:
            raise ValueError("absolute monetary caps must be greater than 0 when set")
        return value

    @field_validator("product_allowlist")
    @classmethod
    def require_spot_products(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Require Coinbase-style USD or USDC spot product ids."""
        pattern = re.compile(SPOT_PRODUCT_ID_PATTERN)
        for product_id in value:
            if not pattern.fullmatch(product_id):
                raise ValueError(
                    "product_allowlist entries must be BASE-USD or BASE-USDC spot products"
                )
        return value

    @field_validator(
        "max_portfolio_exposure_fraction",
        "per_product_max_exposure_fraction",
        "daily_loss_limit_fraction",
        "max_strategy_drawdown_fraction",
        "reference_price_collar_fraction",
    )
    @classmethod
    def require_unit_fraction(cls, value: str) -> str:
        """Keep exposure, breaker, and collar caps in (0, 1]."""
        parsed = Decimal(value)
        if parsed <= 0 or parsed > 1:
            raise ValueError("fractions must be greater than 0 and at most 1")
        return value

    @field_validator("paper_capital_quote")
    @classmethod
    def require_positive_paper_capital(cls, value: str) -> str:
        """Reject a zero paper book."""
        if Decimal(value) <= 0:
            raise ValueError("paper_capital_quote must be greater than 0")
        return value

    @model_validator(mode="after")
    def validate_allocations(self) -> Self:
        """Require unique strategy ids whose reserved quote fits the paper book."""
        strategy_ids = tuple(item.strategy_id for item in self.allocations)
        if len(strategy_ids) != len(set(strategy_ids)):
            raise ValueError("allocations must use unique strategy_id values")
        reserved = sum((Decimal(item.allocated_quote) for item in self.allocations), Decimal("0"))
        if reserved > Decimal(self.paper_capital_quote):
            raise ValueError("sum of allocations cannot exceed paper_capital_quote")
        return self


class RiskPolicyWrite(_FrozenModel):
    """Operator-authored fields for publishing the next immutable policy version."""

    product_allowlist: tuple[str, ...] = Field(default=(), max_length=32)
    max_concurrent_running_deployments: int = Field(ge=1, le=32)
    max_concurrent_open_positions: int = Field(ge=1, le=32)
    max_portfolio_exposure_fraction: DecimalText
    per_product_max_exposure_fraction: DecimalText
    paper_capital_quote: DecimalText
    allocations: tuple[CapitalAllocation, ...] = ()
    daily_loss_limit_fraction: DecimalText = DEFAULT_DAILY_LOSS_LIMIT_FRACTION
    max_strategy_drawdown_fraction: DecimalText = DEFAULT_MAX_STRATEGY_DRAWDOWN_FRACTION
    max_entry_orders_per_minute: int = Field(
        default=DEFAULT_MAX_ENTRY_ORDERS_PER_MINUTE, ge=1, le=1000
    )
    max_cancellations_per_minute: int = Field(
        default=DEFAULT_MAX_CANCELLATIONS_PER_MINUTE, ge=1, le=1000
    )
    reference_price_collar_fraction: DecimalText = DEFAULT_REFERENCE_PRICE_COLLAR_FRACTION
    allow_intra_strategy_pyramiding: bool = False
    max_daily_loss_quote: DecimalText | None = None
    max_portfolio_exposure_quote: DecimalText | None = None
    max_venue_order_actions_per_minute: int | None = Field(default=None, ge=1, le=10000)


class ActiveRiskPolicy(_FrozenModel):
    """Effective policy plus its content identity and provenance."""

    definition: RiskPolicyDefinition
    policy_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source: RiskPolicySource


class RiskVerdict(_FrozenModel):
    """One allow-or-deny decision with a stable reason code."""

    decision: RiskDecision
    reason_code: RiskReasonCode
    detail: str = Field(min_length=1, max_length=500)


def compiled_default_risk_policy() -> RiskPolicyDefinition:
    """Return the compiled conservative multi-asset envelope."""
    return RiskPolicyDefinition(
        policy_id=COMPILED_POLICY_ID,
        version=1,
        product_allowlist=(),
        max_concurrent_running_deployments=8,
        max_concurrent_open_positions=8,
        max_portfolio_exposure_fraction="1",
        per_product_max_exposure_fraction="1",
        paper_capital_quote="100000",
        allocations=(),
        daily_loss_limit_fraction=DEFAULT_DAILY_LOSS_LIMIT_FRACTION,
        max_strategy_drawdown_fraction=DEFAULT_MAX_STRATEGY_DRAWDOWN_FRACTION,
        max_entry_orders_per_minute=DEFAULT_MAX_ENTRY_ORDERS_PER_MINUTE,
        max_cancellations_per_minute=DEFAULT_MAX_CANCELLATIONS_PER_MINUTE,
        reference_price_collar_fraction=DEFAULT_REFERENCE_PRICE_COLLAR_FRACTION,
    )


def canonical_risk_policy_bytes(definition: RiskPolicyDefinition) -> bytes:
    """Serialize one policy into deterministic canonical UTF-8 JSON."""
    validated = RiskPolicyDefinition.model_validate(definition.model_dump(mode="python"))
    payload = validated.model_dump(mode="json")
    if not payload.get("allow_intra_strategy_pyramiding"):
        payload.pop("allow_intra_strategy_pyramiding", None)
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def risk_policy_fingerprint(definition: RiskPolicyDefinition) -> str:
    """Return the SHA-256 identity of the entire canonical policy document."""
    digest = sha256(canonical_risk_policy_bytes(definition)).hexdigest()
    return f"{_FINGERPRINT_PREFIX}{digest}"


def compiled_default_active_policy() -> ActiveRiskPolicy:
    """Return the compiled default wrapped as the effective policy."""
    definition = compiled_default_risk_policy()
    return ActiveRiskPolicy(
        definition=definition,
        policy_fingerprint=risk_policy_fingerprint(definition),
        source=RiskPolicySource.COMPILED_DEFAULT,
    )


def pauses_risk_increasing(reason_code: RiskReasonCode) -> bool:
    """True when a tripped daily-loss or drawdown breaker must pause entries."""
    return reason_code in {
        RiskReasonCode.DAILY_LOSS_LIMIT,
        RiskReasonCode.STRATEGY_DRAWDOWN_LIMIT,
    }


def stored_canonical_fingerprint(raw: str) -> str:
    """Return the SHA-256 identity of stored canonical policy bytes."""
    digest = sha256(raw.encode("utf-8")).hexdigest()
    return f"{_FINGERPRINT_PREFIX}{digest}"


def definition_from_stored_json(raw: str) -> RiskPolicyDefinition:
    """Revalidate stored JSON, overlaying compiled breaker defaults when omitted."""
    loaded: object = json.loads(raw)
    if not isinstance(loaded, dict):
        raise TypeError("canonical risk policy must be a JSON object")
    payload: dict[str, object] = {}
    for key, value in loaded.items():
        if not isinstance(key, str):
            raise TypeError("canonical risk policy keys must be strings")
        payload[key] = value
    defaults = compiled_default_risk_policy()
    overlay: dict[str, object] = {
        "daily_loss_limit_fraction": defaults.daily_loss_limit_fraction,
        "max_strategy_drawdown_fraction": defaults.max_strategy_drawdown_fraction,
        "max_entry_orders_per_minute": defaults.max_entry_orders_per_minute,
        "max_cancellations_per_minute": defaults.max_cancellations_per_minute,
        "reference_price_collar_fraction": defaults.reference_price_collar_fraction,
    }
    for key in _LEGACY_BREAKER_KEYS:
        payload.setdefault(key, overlay[key])
    return RiskPolicyDefinition.model_validate(payload)
