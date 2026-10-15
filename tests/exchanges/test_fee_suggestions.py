"""Research/paper fee suggestions are the account's billed Coinbase rates (ADR 0090, 0144)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from thytrader.exchanges.fee_suggestions import ResearchFeeSuggestion, suggest_research_fee_rates
from thytrader.exchanges.fees import FeeProfile


def _profile(*, maker: str = "0.005", taker: str = "0.009") -> FeeProfile:
    """One UTC fee snapshot used as suggestion input."""
    return FeeProfile(
        taker_fee_rate=Decimal(taker),
        maker_fee_rate=Decimal(maker),
        usd_volume_30d=Decimal("710.4"),
        fee_tier="Intro",
        as_of=datetime(2026, 10, 15, 21, tzinfo=UTC),
        source="coinbase",
    )


def test_demo_suggests_nothing() -> None:
    """Demo snapshots never become research suggestions."""
    suggestion = suggest_research_fee_rates(profile=_profile(), demo=True)
    assert suggestion.source == "unavailable"
    assert suggestion.unavailable_reason == "demo_or_missing_credentials"
    assert suggestion.suggested_maker_fee_rate is None
    assert suggestion.fee_tier is None


def test_live_snapshot_suggests_the_billed_account_rates() -> None:
    """Live credentials prefill exactly what Coinbase bills this account."""
    profile = _profile()
    suggestion = suggest_research_fee_rates(profile=profile, demo=False)
    assert suggestion.source == "coinbase_account"
    assert suggestion.suggested_maker_fee_rate == Decimal("0.005")
    assert suggestion.suggested_taker_fee_rate == Decimal("0.009")
    assert suggestion.fee_tier == "Intro"
    assert suggestion.fetched_at == profile.as_of


def test_suggestions_keep_their_source_consistent() -> None:
    """An account suggestion needs rates; an unavailable one carries none."""
    with pytest.raises(ValueError, match="require rates"):
        ResearchFeeSuggestion(source="coinbase_account", fee_tier="Intro")
    with pytest.raises(ValueError, match="must not invent"):
        ResearchFeeSuggestion(
            source="unavailable",
            unavailable_reason="demo_or_missing_credentials",
            suggested_maker_fee_rate=Decimal("0.005"),
        )
