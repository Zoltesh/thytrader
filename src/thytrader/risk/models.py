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

from thytrader.strategies.models import DecimalText

RISK_POLICY_SCHEMA_VERSION: Literal["thytrader-risk-policy-v1"] = "thytrader-risk-policy-v1"
COMPILED_POLICY_ID = UUID("01978a3e-5f2c-7d10-b3a4-0000000000aa")
_FINGERPRINT_PREFIX = "sha256:"
_PRODUCT_PATTERN = r"^[A-Z0-9]{2,20}-USD$"


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
    quote_currency: Literal["USD"] = "USD"
    product_allowlist: tuple[str, ...] = Field(default=(), max_length=32)
    max_concurrent_running_deployments: int = Field(ge=1, le=32)
    max_concurrent_open_positions: int = Field(ge=1, le=32)
    max_portfolio_exposure_fraction: DecimalText
    per_product_max_exposure_fraction: DecimalText
    paper_capital_quote: DecimalText
    allocations: tuple[CapitalAllocation, ...] = ()

    @field_validator("product_allowlist")
    @classmethod
    def require_unique_allowlist(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject duplicate or malformed product identifiers."""
        if len(value) != len(set(value)):
            raise ValueError("product_allowlist must be unique")
        return value

    @field_validator("product_allowlist")
    @classmethod
    def require_usd_spot_products(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Require Coinbase-style USD spot product ids."""
        pattern = re.compile(_PRODUCT_PATTERN)
        for product_id in value:
            if not pattern.fullmatch(product_id):
                raise ValueError("product_allowlist entries must be BASE-USD spot products")
        return value

    @field_validator("max_portfolio_exposure_fraction", "per_product_max_exposure_fraction")
    @classmethod
    def require_unit_fraction(cls, value: str) -> str:
        """Keep exposure caps in (0, 1]."""
        parsed = Decimal(value)
        if parsed <= 0 or parsed > 1:
            raise ValueError("exposure fractions must be greater than 0 and at most 1")
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
    )


def canonical_risk_policy_bytes(definition: RiskPolicyDefinition) -> bytes:
    """Serialize one policy into deterministic canonical UTF-8 JSON."""
    validated = RiskPolicyDefinition.model_validate(definition.model_dump(mode="python"))
    payload = validated.model_dump(mode="json")
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
