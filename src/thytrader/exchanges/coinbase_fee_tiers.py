"""Read the account's Coinbase fee-tier ladder from its transaction summary (ADR 0144).

``GET /api/v3/brokerage/transaction_summary`` reports, beside the billed ``fee_tier``, a
``fee_tier_without_promotion`` block: the current and next tier (rates plus the spot-volume
and asset ranges that qualify), how this account qualifies, its current value and the next
tier's threshold. Coinbase publishes no endpoint for the whole schedule, so ThyTrader keeps
none: it shows what the source reports. Every field is optional and read best effort; a
missing or malformed ladder never fails the fee read itself.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, cast

from pydantic import ValidationError

from thytrader.exchanges.fees import FeeTierBand

_QUALIFICATION_PREFIX = "FEE_TIER_QUALIFICATION_TYPE_"


def tier_ladder(payload: dict[str, Any]) -> dict[str, Any]:
    """``current_tier``, ``next_tier`` and qualification fields for ``FeeProfile``."""
    ladder = _mapping(payload.get("fee_tier_without_promotion"))
    if ladder is None:
        return {}
    qualification = ladder.get("qualification_type")
    return {
        "current_tier": _band(ladder.get("current_tier")),
        "next_tier": _band(ladder.get("next_tier")),
        "qualification": (
            qualification.removeprefix(_QUALIFICATION_PREFIX).lower()
            if isinstance(qualification, str) and qualification
            else None
        ),
        "qualification_value": _decimal(ladder.get("current_value")),
        "next_tier_threshold": _decimal(ladder.get("next_tier_threshold")),
    }


def _band(raw: object) -> FeeTierBand | None:
    """One reported tier, or None when it is absent or incomplete."""
    tier = _mapping(raw)
    if tier is None:
        return None
    spot = _spot_volume_range(tier)
    try:
        return FeeTierBand(
            pricing_tier=str(tier.get("pricing_tier") or "").strip(),
            maker_fee_rate=_required(tier.get("maker_fee_rate")),
            taker_fee_rate=_required(tier.get("taker_fee_rate")),
            spot_volume_from=spot[0],
            spot_volume_to=spot[1],
            assets_from=_decimal(tier.get("aop_from")),
            assets_to=_upper(tier.get("aop_to")),
        )
    except ValidationError, ValueError, TypeError:
        return None


def _spot_volume_range(tier: dict[str, Any]) -> tuple[Decimal | None, Decimal | None]:
    """The spot volume range: the typed ranges first, else the legacy USD bounds."""
    ranges = tier.get("volume_types_and_range")
    if isinstance(ranges, list):
        for item in ranges:
            entry = _mapping(item)
            if entry is not None and "VOLUME_TYPE_SPOT" in (entry.get("volume_types") or []):
                return _decimal(entry.get("vol_from")), _upper(entry.get("vol_to"))
    return _decimal(tier.get("usd_from")), _upper(tier.get("usd_to"))


def _mapping(raw: object) -> dict[str, Any] | None:
    """A response object as a dict (SDK objects expose ``to_dict``)."""
    if isinstance(raw, dict):
        return cast("dict[str, Any]", raw)
    to_dict = getattr(raw, "to_dict", None)
    if callable(to_dict):
        converted = to_dict()
        return cast("dict[str, Any]", converted) if isinstance(converted, dict) else None
    return None


def _decimal(raw: object) -> Decimal | None:
    """A finite non-negative decimal, or None."""
    if raw is None or isinstance(raw, bool) or raw == "":
        return None
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        return None
    return value if value.is_finite() and value >= 0 else None


def _upper(raw: object) -> Decimal | None:
    """An upper bound; zero or absent means open-ended."""
    value = _decimal(raw)
    return None if value is None or value == 0 else value


def _required(raw: object) -> Decimal:
    """A decimal that must be present."""
    value = _decimal(raw)
    if value is None:
        raise ValueError("missing rate")
    return value
