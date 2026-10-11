"""The optional ``futures`` block of the risk policy (ADR 0129).

P1-2a ships the two fields of the live spot collateral gate (ADR 0129 §2 L3). P1-4 adds the
paper futures envelope and the futures-scope daily loss limit (§4, §7); P1-5 adds the rest
of the futures entry gate (§5) and the opt-in base-unit beta netting (§6). P2-3 adds the
live futures opt-in (ADR 0134 §2): ``live_enabled``, ``live_capital_usd``, a futures
``product_allowlist``, ``live_derisk_margin_ratio`` and ``live_funding_drift_tolerance_usd``.
The block is excluded from the canonical policy document while unset, and each optional
field while unset, so every existing policy keeps its fingerprint.
"""

from __future__ import annotations

from decimal import Decimal
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from thytrader.market_data.instrument_ids import FUTURES_PRODUCT_ID_PATTERN
from thytrader.strategies.models import DecimalText

DEFAULT_PEG_HAIRCUT = "1.25"
DEFAULT_LIQUIDATION_BUFFER_FRACTION = Decimal("0.5")
"""The liquidation buffer a futures entry keeps while the policy leaves it unset (ADR 0128)."""
_MAX_FUTURES_LEVERAGE = Decimal(20)
_MAX_FUNDING_RATE = Decimal("0.01")
_MAX_DERISK_MARGIN_RATIO = Decimal(100)
MAX_FUTURES_ALLOWLISTED_PRODUCTS = 32
"""Upper bound on ``futures.product_allowlist`` entries."""
DEFAULT_LIVE_MAX_ORDER_CONTRACTS = 1
"""The live per-order and position contract cap while ``max_order_contracts`` is unset."""


class FuturesRiskPolicy(BaseModel):
    """Futures-related risk settings.

    ``live_spot_collateral_reserve_quote`` is an amount in the policy quote that live spot
    books leave untouched for futures margin. While manual futures are ``in_use``, live
    USDC/USD spot entries continue only when it is set, it covers the CFM initial margin
    times ``peg_haircut`` (a yes/no threshold check; the two currencies are never added),
    and it is withheld from the spot capital base. ``peg_haircut`` is ≥ 1.0.

    ``paper_capital_usd`` is the simulated USD envelope paper futures books draw from; it is
    separate from ``paper_capital_quote`` and an unset value refuses a paper futures start.
    ``daily_loss_limit_fraction`` bounds the futures-scope daily loss (change in paper futures
    book equity since the UTC day open) as a fraction of ``paper_capital_usd``; unset means
    the policy's ``daily_loss_limit_fraction`` applies to that USD capital, and
    ``max_daily_loss_usd`` is an optional absolute ceiling on it.

    The futures entry gate (ADR 0129 §5): ``max_leverage`` (gross notional over futures
    book equity; the lower of it and the strategy's applies), ``min_liquidation_buffer_fraction``
    ((equity - maintenance) / equity after the entry; 0.5 while unset),
    ``max_exposure_fraction`` (gross futures notional over ``paper_capital_usd``),
    ``max_order_contracts`` (per order), ``max_hourly_funding_rate_abs`` (deny while the
    latest settled hourly rate exceeds it) and ``max_btc_beta_exposure_fraction`` (the futures
    scope's own BTC-beta cap over ``paper_capital_usd``). ``beta_netting`` ``net_by_underlying``
    lets managed same-mode futures positions net, in base units, against same-underlying spot
    inventory for the spot BTC-beta cap only (§6); unset is ``gross``.

    Live futures (ADR 0134 §2, I2): starts and entries need ``live_enabled: true``,
    ``live_capital_usd`` (the USD envelope live futures allocations draw from, and the capital
    of the live ``CFM-USD`` daily loss and exposure caps) and the product on
    ``product_allowlist`` (futures ids only; the policy's top-level allowlist stays spot-only).
    A non-empty ``product_allowlist`` also restricts paper futures books. Live orders and the
    live position hold at most ``max_order_contracts`` contracts, **1** while it is unset.
    ``live_derisk_margin_ratio`` (> 1, on the venue's ``available_margin /
    liquidation_threshold``) and ``live_funding_drift_tolerance_usd`` are stored for the
    future live margin monitor and funding drift alert. The opt-in never gates exits or
    protection.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    live_spot_collateral_reserve_quote: DecimalText | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    peg_haircut: DecimalText = DEFAULT_PEG_HAIRCUT
    paper_capital_usd: DecimalText | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    daily_loss_limit_fraction: DecimalText | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    max_daily_loss_usd: DecimalText | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    max_leverage: DecimalText | None = Field(default=None, exclude_if=lambda value: value is None)
    min_liquidation_buffer_fraction: DecimalText | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    max_exposure_fraction: DecimalText | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    max_order_contracts: int | None = Field(
        default=None, ge=1, le=1_000_000, exclude_if=lambda value: value is None
    )
    max_hourly_funding_rate_abs: DecimalText | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    max_btc_beta_exposure_fraction: DecimalText | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    beta_netting: Literal["gross", "net_by_underlying"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    live_enabled: bool | None = Field(
        default=None, strict=True, exclude_if=lambda value: value is None
    )
    live_capital_usd: DecimalText | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    product_allowlist: tuple[str, ...] | None = Field(
        default=None,
        max_length=MAX_FUTURES_ALLOWLISTED_PRODUCTS,
        exclude_if=lambda value: value is None,
    )
    live_derisk_margin_ratio: DecimalText | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    live_funding_drift_tolerance_usd: DecimalText | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @property
    def liquidation_buffer_fraction(self) -> Decimal:
        """The effective buffer: the policy's, else the 0.5 default."""
        if self.min_liquidation_buffer_fraction is None:
            return DEFAULT_LIQUIDATION_BUFFER_FRACTION
        return Decimal(self.min_liquidation_buffer_fraction)

    @property
    def nets_by_underlying(self) -> bool:
        """Whether managed futures may net against spot for the spot beta cap."""
        return self.beta_netting == "net_by_underlying"

    @property
    def live_opted_in(self) -> bool:
        """Whether the operator turned live futures on (``live_enabled: true``)."""
        return self.live_enabled is True

    @property
    def effective_live_max_order_contracts(self) -> int:
        """The live per-order and position cap: ``max_order_contracts``, else 1 (I5)."""
        if self.max_order_contracts is None:
            return DEFAULT_LIVE_MAX_ORDER_CONTRACTS
        return self.max_order_contracts

    def allowlists(self, product_id: str) -> bool:
        """Whether a non-empty futures allowlist names ``product_id``."""
        return product_id in (self.product_allowlist or ())

    @field_validator("product_allowlist")
    @classmethod
    def require_futures_products(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        """Unique ``CODE-DDMONYY-CDE`` futures ids; an empty list is unset."""
        if not value:
            return None
        if len(value) != len(set(value)):
            raise ValueError("futures product_allowlist must be unique")
        pattern = re.compile(FUTURES_PRODUCT_ID_PATTERN)
        for product_id in value:
            if not pattern.fullmatch(product_id):
                raise ValueError(
                    "futures product_allowlist entries must be CODE-DDMONYY-CDE futures ids"
                )
        return value

    @field_validator("live_derisk_margin_ratio")
    @classmethod
    def require_derisk_ratio(cls, value: str | None) -> str | None:
        """The de-risk margin ratio lies in (1, 100]: the venue liquidates at 1."""
        if value is not None and not Decimal(1) < Decimal(value) <= _MAX_DERISK_MARGIN_RATIO:
            raise ValueError("futures live_derisk_margin_ratio must be in (1, 100]")
        return value

    @field_validator("max_daily_loss_usd")
    @classmethod
    def require_positive_loss(cls, value: str | None) -> str | None:
        """An absolute loss ceiling is a positive USD amount."""
        if value is not None and Decimal(value) <= 0:
            raise ValueError("futures max_daily_loss_usd must be greater than 0")
        return value

    @field_validator("max_leverage")
    @classmethod
    def require_leverage(cls, value: str | None) -> str | None:
        """Leverage lies in [1, 20]."""
        if value is not None and not Decimal(1) <= Decimal(value) <= _MAX_FUTURES_LEVERAGE:
            raise ValueError("futures max_leverage must be between 1 and 20")
        return value

    @field_validator("min_liquidation_buffer_fraction")
    @classmethod
    def require_buffer(cls, value: str | None) -> str | None:
        """The buffer lies in [0, 1)."""
        if value is not None and not Decimal(0) <= Decimal(value) < 1:
            raise ValueError("futures min_liquidation_buffer_fraction must be in [0, 1)")
        return value

    @field_validator("max_exposure_fraction", "max_btc_beta_exposure_fraction")
    @classmethod
    def require_levered_fraction(cls, value: str | None) -> str | None:
        """Futures exposure fractions are gross notional over capital, in (0, 20]."""
        if value is not None and not Decimal(0) < Decimal(value) <= _MAX_FUTURES_LEVERAGE:
            raise ValueError("futures exposure fractions must be in (0, 20]")
        return value

    @field_validator("max_hourly_funding_rate_abs")
    @classmethod
    def require_funding_cap(cls, value: str | None) -> str | None:
        """A funding-rate cap lies in (0, 0.01] per hour."""
        if value is not None and not Decimal(0) < Decimal(value) <= _MAX_FUNDING_RATE:
            raise ValueError("futures max_hourly_funding_rate_abs must be in (0, 0.01]")
        return value

    @field_validator(
        "live_spot_collateral_reserve_quote",
        "paper_capital_usd",
        "live_capital_usd",
        "live_funding_drift_tolerance_usd",
    )
    @classmethod
    def require_positive_amount(cls, value: str | None) -> str | None:
        """A reserve or envelope, when set, is a positive amount."""
        if value is not None and Decimal(value) <= 0:
            raise ValueError("futures amounts must be greater than 0")
        return value

    @field_validator("daily_loss_limit_fraction")
    @classmethod
    def require_unit_fraction(cls, value: str | None) -> str | None:
        """A daily loss fraction lies in (0, 1]."""
        if value is not None and not Decimal(0) < Decimal(value) <= 1:
            raise ValueError("futures daily_loss_limit_fraction must be in (0, 1]")
        return value

    @field_validator("peg_haircut")
    @classmethod
    def require_haircut_at_least_one(cls, value: str) -> str:
        """A haircut below 1.0 would let a smaller USDC reserve cover larger USD margin."""
        if Decimal(value) < 1:
            raise ValueError("peg_haircut must be at least 1.0")
        return value
