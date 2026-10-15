"""The account's Coinbase tier ladder is read from its transaction summary (ADR 0144)."""

from decimal import Decimal

from thytrader.exchanges.coinbase_fee_tiers import tier_ladder


def _tier(name: str, maker: str, taker: str, spot: tuple[str, str], aop: tuple[str, str]) -> dict:
    """One tier object shaped like Coinbase's ``FeeTier``."""
    return {
        "pricing_tier": name,
        "maker_fee_rate": maker,
        "taker_fee_rate": taker,
        "usd_from": spot[0],
        "usd_to": spot[1],
        "aop_from": aop[0],
        "aop_to": aop[1],
        "volume_types_and_range": [
            {"volume_types": ["VOLUME_TYPE_SPOT"], "vol_from": spot[0], "vol_to": spot[1]},
            {"volume_types": ["VOLUME_TYPE_OPTIONS"], "vol_from": "0", "vol_to": "100000"},
        ],
    }


_PAYLOAD = {
    "fee_tier": _tier("Intro", "0.005", "0.009", ("0", "10000"), ("0", "500000")),
    "fee_tier_without_promotion": {
        "pricing_tier": "",
        "qualification_type": "FEE_TIER_QUALIFICATION_TYPE_SPOT_VOLUME",
        "current_value": "710.4046676654727",
        "next_tier_threshold": "10000",
        "current_tier": _tier("Intro", "0.005", "0.009", ("0", "10000"), ("0", "500000")),
        "next_tier": _tier(
            "Advanced 1", "0.0035", "0.0075", ("10000", "50000"), ("500000", "1000000")
        ),
        "current_tier_threshold": "0",
    },
}


def test_the_current_and_next_tier_come_from_coinbase() -> None:
    """Rates, spot-volume and asset ranges, and the account's progress are as reported."""
    ladder = tier_ladder(_PAYLOAD)
    current, upcoming = ladder["current_tier"], ladder["next_tier"]
    assert (current.pricing_tier, current.maker_fee_rate, current.taker_fee_rate) == (
        "Intro",
        Decimal("0.005"),
        Decimal("0.009"),
    )
    assert (upcoming.pricing_tier, upcoming.maker_fee_rate, upcoming.taker_fee_rate) == (
        "Advanced 1",
        Decimal("0.0035"),
        Decimal("0.0075"),
    )
    assert (upcoming.spot_volume_from, upcoming.spot_volume_to) == (
        Decimal("10000"),
        Decimal("50000"),
    )
    assert (upcoming.assets_from, upcoming.assets_to) == (Decimal("500000"), Decimal("1000000"))
    assert ladder["qualification"] == "spot_volume"
    assert ladder["qualification_value"] == Decimal("710.4046676654727")
    assert ladder["next_tier_threshold"] == Decimal("10000")


def test_the_top_tier_has_no_next_tier_and_open_upper_bounds() -> None:
    """Coinbase omits ``next_tier`` at the top; a zero upper bound means open-ended."""
    payload = {
        "fee_tier_without_promotion": {
            "qualification_type": "FEE_TIER_QUALIFICATION_TYPE_SPOT_VOLUME",
            "current_tier": _tier("VIP", "0", "0.0005", ("400000000", "0"), ("0", "0")),
        }
    }
    ladder = tier_ladder(payload)
    assert ladder["next_tier"] is None
    assert ladder["current_tier"].spot_volume_to is None
    assert ladder["current_tier"].assets_to is None


def test_a_missing_or_malformed_ladder_is_simply_absent() -> None:
    """The fee read never fails for want of the ladder."""
    assert tier_ladder({"fee_tier": {}}) == {}
    broken = tier_ladder(
        {
            "fee_tier_without_promotion": {
                "current_tier": {"pricing_tier": "Intro", "maker_fee_rate": "x"},
                "next_tier": "nonsense",
                "current_value": "-5",
            }
        }
    )
    assert broken["current_tier"] is None
    assert broken["next_tier"] is None
    assert broken["qualification_value"] is None


def test_hostile_ladder_shapes_never_raise() -> None:
    """Non-list types, oversized labels and odd objects leave the ladder empty or partial."""
    weird = {
        "fee_tier_without_promotion": {
            "qualification_type": "FEE_TIER_QUALIFICATION_TYPE_" + "X" * 200,
            "current_tier": {
                "pricing_tier": "Intro",
                "maker_fee_rate": "0.005",
                "taker_fee_rate": "0.009",
                "volume_types_and_range": [{"volume_types": 1, "vol_from": "0"}],
            },
            "next_tier": {"pricing_tier": "Advanced 1", "volume_types_and_range": 5},
        }
    }
    ladder = tier_ladder(weird)
    assert ladder["qualification"] is None
    assert ladder["current_tier"] is not None
    assert ladder["next_tier"] is None
    assert tier_ladder({"fee_tier_without_promotion": 42}) == {}
