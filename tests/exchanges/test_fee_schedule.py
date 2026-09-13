"""Tests for the versioned Coinbase spot fee schedule used as research suggestions."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from thytrader.exchanges.fee_schedule import (
    COINBASE_SPOT_FEE_BANDS,
    COINBASE_SPOT_FEE_SCHEDULE_AS_OF,
    COINBASE_SPOT_FEE_SCHEDULE_VERSION,
    lookup_coinbase_spot_fee_band,
    lookup_coinbase_spot_fee_band_by_label,
    lookup_coinbase_spot_fee_band_by_volume,
    suggest_research_fee_rates,
)
from thytrader.exchanges.fees import FeeProfile


def _profile(
    *,
    fee_tier: str,
    usd_volume_30d: str,
    maker_fee_rate: str = "0.0040",
    taker_fee_rate: str = "0.0060",
) -> FeeProfile:
    """Return one UTC fee snapshot used as suggestion input."""
    return FeeProfile(
        taker_fee_rate=Decimal(taker_fee_rate),
        maker_fee_rate=Decimal(maker_fee_rate),
        usd_volume_30d=Decimal(usd_volume_30d),
        fee_tier=fee_tier,
        as_of=datetime(2026, 9, 13, 16, 0, tzinfo=UTC),
        source="coinbase",
    )


def test_schedule_version_and_as_of_are_pinned() -> None:
    """Suggestions cite a versioned table, not a live pointer to Coinbase's current page."""
    assert COINBASE_SPOT_FEE_SCHEDULE_VERSION == "coinbase-advanced-spot-fees-v1"
    assert COINBASE_SPOT_FEE_SCHEDULE_AS_OF.isoformat() == "2026-09-13"


def test_volume_bands_cover_non_negative_volume_without_overlap() -> None:
    """Each published band is contiguous, lower-inclusive, and upper-exclusive except the last."""
    assert COINBASE_SPOT_FEE_BANDS[0].usd_volume_from == Decimal("0")
    assert COINBASE_SPOT_FEE_BANDS[-1].usd_volume_to is None
    for index, band in enumerate(COINBASE_SPOT_FEE_BANDS[:-1]):
        nxt = COINBASE_SPOT_FEE_BANDS[index + 1]
        assert band.usd_volume_to == nxt.usd_volume_from
        assert band.contains_usd_volume(band.usd_volume_from)
        assert band.usd_volume_to is not None
        assert not band.contains_usd_volume(band.usd_volume_to)
        assert nxt.contains_usd_volume(band.usd_volume_to)


@pytest.mark.parametrize(
    ("volume", "tier_id", "maker", "taker"),
    [
        ("0", "usd-0-10k", "0.0040", "0.0060"),
        ("9999.99", "usd-0-10k", "0.0040", "0.0060"),
        ("10000", "usd-10k-50k", "0.0025", "0.0040"),
        ("49999.99", "usd-10k-50k", "0.0025", "0.0040"),
        ("50000", "usd-50k-100k", "0.0015", "0.0025"),
        ("100000", "usd-100k-1m", "0.0010", "0.0020"),
        ("1000000", "usd-1m-15m", "0.0008", "0.0018"),
        ("15000000", "usd-15m-75m", "0.0006", "0.0016"),
        ("75000000", "usd-75m-250m", "0.0003", "0.0010"),
        ("250000000", "usd-250m-400m", "0.0000", "0.0006"),
        ("400000000", "usd-400m-plus", "0.0000", "0.0004"),
        ("900000000", "usd-400m-plus", "0.0000", "0.0004"),
    ],
)
def test_volume_lookup_matches_published_bps(
    volume: str, tier_id: str, maker: str, taker: str
) -> None:
    """Volume lookup returns the pinned maker/taker bps for each published band."""
    band = lookup_coinbase_spot_fee_band_by_volume(Decimal(volume))
    assert band.tier_id == tier_id
    assert band.maker_fee_rate == Decimal(maker)
    assert band.taker_fee_rate == Decimal(taker)


@pytest.mark.parametrize(
    ("label", "tier_id"),
    [
        ("Tier 1", "usd-0-10k"),
        ("tier 1", "usd-0-10k"),
        ("Advanced 1", "usd-0-10k"),
        ("Tier 2 ($10k-$50k)", "usd-10k-50k"),
        ("Advanced 9", "usd-400m-plus"),
    ],
)
def test_label_lookup_maps_coinbase_pricing_tier_names(label: str, tier_id: str) -> None:
    """Known Coinbase pricing_tier strings map to the pinned schedule band."""
    band = lookup_coinbase_spot_fee_band_by_label(label)
    assert band is not None
    assert band.tier_id == tier_id


def test_unknown_label_falls_back_to_volume() -> None:
    """Unrecognized tier names do not invent a band; volume is the published schedule key."""
    assert lookup_coinbase_spot_fee_band_by_label("Liquidity Plus") is None
    band = lookup_coinbase_spot_fee_band(fee_tier="Liquidity Plus", usd_volume_30d=Decimal("25000"))
    assert band.tier_id == "usd-10k-50k"
    assert band.maker_fee_rate == Decimal("0.0025")
    assert band.taker_fee_rate == Decimal("0.0040")


def test_known_label_wins_when_volume_disagrees() -> None:
    """Coinbase's pricing_tier is the billed tier even if 30-day volume is in another band."""
    band = lookup_coinbase_spot_fee_band(
        fee_tier="Tier 2 ($10k-$50k)",
        usd_volume_30d=Decimal("125000.50"),
    )
    assert band.tier_id == "usd-10k-50k"
    assert band.maker_fee_rate == Decimal("0.0025")
    assert band.taker_fee_rate == Decimal("0.0040")


def test_negative_volume_is_rejected() -> None:
    """Negative volume is missing evidence, not a mapped tier."""
    with pytest.raises(ValueError, match="non-negative"):
        lookup_coinbase_spot_fee_band_by_volume(Decimal("-1"))


def test_demo_does_not_map_a_fake_tier() -> None:
    """Demo snapshots must not become research suggestions via the real schedule."""
    suggestion = suggest_research_fee_rates(
        profile=_profile(fee_tier="Tier 1", usd_volume_30d="15250.00"),
        demo=True,
    )
    assert suggestion.source == "unavailable"
    assert suggestion.unavailable_reason == "demo_or_missing_credentials"
    assert suggestion.suggested_maker_fee_rate is None
    assert suggestion.suggested_taker_fee_rate is None
    assert suggestion.schedule_version is None


def test_live_snapshot_suggests_schedule_rates_and_source_metadata() -> None:
    """Live credentials prefill from the pinned schedule and record what was mapped."""
    profile = _profile(
        fee_tier="Tier 2 ($10k-$50k)",
        usd_volume_30d="25000",
        maker_fee_rate="0.0025",
        taker_fee_rate="0.0040",
    )
    suggestion = suggest_research_fee_rates(profile=profile, demo=False)
    assert suggestion.source == "coinbase_fee_schedule"
    assert suggestion.suggested_maker_fee_rate == Decimal("0.0025")
    assert suggestion.suggested_taker_fee_rate == Decimal("0.0040")
    assert suggestion.fee_tier == "Tier 2 ($10k-$50k)"
    assert suggestion.schedule_tier_id == "usd-10k-50k"
    assert suggestion.schedule_version == COINBASE_SPOT_FEE_SCHEDULE_VERSION
    assert suggestion.schedule_as_of == COINBASE_SPOT_FEE_SCHEDULE_AS_OF
    assert suggestion.fetched_at == profile.as_of
    assert suggestion.unavailable_reason is None
