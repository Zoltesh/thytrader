"""Live-start evidence: what an exact strategy fingerprint has shown, and when it is thin."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from thytrader.research.live_evidence import (
    LIVE_EVIDENCE_REQUIRED,
    LIVE_EVIDENCE_THIN,
    assess_live_start_evidence,
    gather_live_start_evidence,
    live_evidence_message,
)
from thytrader.research.promotion import EvidenceWindow, PromotionEvidence
from thytrader.risk.start_safeguards import LiveEvidenceThresholds
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentStatus,
    ExecutionStoreError,
    RuntimePhase,
)

_NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
_FINGERPRINT = "sha256:" + "a" * 64
_OTHER = "sha256:" + "b" * 64


def _window(trades: int, net: str, *, fingerprint: str = _FINGERPRINT) -> EvidenceWindow:
    """One out-of-sample window of ``fingerprint``."""
    return EvidenceWindow(
        label="fold-1",
        role="out_of_sample",
        result_fingerprint="sha256:" + "c" * 64,
        strategy_fingerprint=fingerprint,
        total_net_pnl=net,
        trade_count=trades,
    )


def _evidence(*windows: EvidenceWindow) -> PromotionEvidence:
    """Promotion evidence holding ``windows`` as its out-of-sample windows."""
    return PromotionEvidence(strategy_fingerprint=_FINGERPRINT, out_of_sample=windows)


def test_evidence_meeting_every_threshold_is_not_thin() -> None:
    """Enough positive out-of-sample trades and paper days: nothing to warn about."""
    assessed = assess_live_start_evidence(
        _evidence(_window(20, "12.5"), _window(15, "-2.5")),
        paper_created_at=(_NOW - timedelta(days=20), _NOW - timedelta(days=3)),
        thresholds=LiveEvidenceThresholds(),
        now=_NOW,
    )
    assert not assessed.thin
    assert assessed.out_of_sample_windows == 2
    assert assessed.out_of_sample_trades == 35
    assert assessed.out_of_sample_net_pnl == "10.0"
    assert assessed.paper_deployments == 2
    assert assessed.paper_days == "20.0"


def test_no_research_and_no_paper_is_thin_with_both_reasons() -> None:
    """A strategy never studied out of sample nor paper traded names both gaps."""
    assessed = assess_live_start_evidence(
        _evidence(), paper_created_at=(), thresholds=LiveEvidenceThresholds(), now=_NOW
    )
    assert assessed.thin
    assert assessed.out_of_sample_net_pnl is None
    assert assessed.paper_days == "0"
    assert assessed.thin_reasons == (
        "no out-of-sample study window for this exact strategy fingerprint",
        "no paper book of this exact strategy fingerprint",
    )


def test_each_unmet_threshold_is_named() -> None:
    """Too few trades, a losing result and too few paper days are each a reason."""
    assessed = assess_live_start_evidence(
        _evidence(_window(12, "-3.1")),
        paper_created_at=(_NOW - timedelta(days=2, hours=3),),
        thresholds=LiveEvidenceThresholds(),
        now=_NOW,
    )
    assert assessed.thin_reasons == (
        "12 out-of-sample trades, fewer than 30",
        "out-of-sample net result -3.1 is not positive",
        "2.1 days since its first paper book, fewer than 14",
    )


def test_thresholds_the_operator_relaxed_are_not_required() -> None:
    """Zero trades, any result and zero paper days: no research or paper is needed."""
    relaxed = LiveEvidenceThresholds(
        min_out_of_sample_trades=0, require_positive_out_of_sample=False, min_paper_days=0
    )
    assessed = assess_live_start_evidence(
        _evidence(),
        paper_created_at=(),
        thresholds=relaxed,
        now=_NOW,
        unavailable=("studies", "paper_books"),
    )
    assert not assessed.thin
    assert assessed.unavailable == ("studies", "paper_books")


def test_an_unread_source_a_threshold_needs_is_thin() -> None:
    """Nothing is known from a source that could not be read."""
    assessed = assess_live_start_evidence(
        _evidence(),
        paper_created_at=(),
        thresholds=LiveEvidenceThresholds(),
        now=_NOW,
        unavailable=("studies", "paper_books"),
    )
    assert assessed.thin_reasons == ("studies could not be read", "paper books could not be read")


def test_the_message_warns_or_refuses() -> None:
    """The warning says the bot started; the refusal says nothing did and how to proceed."""
    assessed = assess_live_start_evidence(
        _evidence(), paper_created_at=(), thresholds=LiveEvidenceThresholds(), now=_NOW
    )
    warning = live_evidence_message(assessed, refused=False)
    refusal = live_evidence_message(assessed, refused=True)
    assert warning.startswith(f"{LIVE_EVIDENCE_THIN}: ")
    assert "Started anyway" in warning
    assert "require_live_evidence" in warning
    assert refusal.startswith(f"{LIVE_EVIDENCE_REQUIRED}: ")
    assert "Nothing was started" in refusal
    assert "no paper book of this exact strategy fingerprint" in refusal


def _paper(strategy_id: UUID, fingerprint: str, created_at: datetime) -> Deployment:
    """One paper book of ``fingerprint`` created at ``created_at``."""
    return Deployment(
        id=uuid4(),
        strategy_id=strategy_id,
        strategy_fingerprint=fingerprint,
        product_id="BTC-USD",
        timeframe="1h",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.STOPPED,
        phase=RuntimePhase.FLAT,
        cash=Decimal(1000),
        created_at=created_at,
        updated_at=created_at,
    )


@pytest.mark.anyio
async def test_gathering_counts_only_paper_books_of_the_exact_fingerprint() -> None:
    """Paper books of another revision of the strategy are not this fingerprint's evidence."""
    store = InMemoryExecutionStore()
    strategy_id = uuid4()
    await store.create_deployment(_paper(strategy_id, _FINGERPRINT, _NOW - timedelta(days=15)))
    await store.create_deployment(_paper(strategy_id, _OTHER, _NOW - timedelta(days=90)))
    gathered = await gather_live_start_evidence(
        strategy_fingerprint=_FINGERPRINT,
        strategy_id=strategy_id,
        catalog=None,
        execution=store,
        thresholds=LiveEvidenceThresholds(min_out_of_sample_trades=0, min_paper_days=14),
        now=_NOW,
    )
    assert gathered.paper_deployments == 1
    assert gathered.paper_days == "15.0"
    assert gathered.unavailable == ("studies",)
    assert gathered.thin_reasons == ("studies could not be read",)


class _Unreadable(InMemoryExecutionStore):
    """An execution store whose strategy listing fails."""

    async def list_by_strategy(self, strategy_id: str) -> tuple[Deployment, ...]:
        """Fail like an unavailable database."""
        del strategy_id
        raise ExecutionStoreError("Execution storage is unavailable.")


@pytest.mark.anyio
async def test_gathering_never_raises_on_an_unreadable_store() -> None:
    """A failed read is reported as unavailable evidence, not an error."""
    gathered = await gather_live_start_evidence(
        strategy_fingerprint=_FINGERPRINT,
        strategy_id=uuid4(),
        catalog=None,
        execution=_Unreadable(),
        thresholds=LiveEvidenceThresholds(),
        now=_NOW,
    )
    assert gathered.unavailable == ("studies", "paper_books")
    assert gathered.thin
