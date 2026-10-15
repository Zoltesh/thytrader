"""Live-start evidence: what an exact strategy fingerprint has shown, and when it is thin."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import pytest

from thytrader.research import live_evidence as live_evidence_module
from thytrader.research.catalog import StudyCatalogIntegrityError
from thytrader.research.live_evidence import (
    LIVE_EVIDENCE_REQUIRED,
    LIVE_EVIDENCE_THIN,
    assess_live_start_evidence,
    gather_live_start_evidence,
    live_evidence_message,
)
from thytrader.research.promotion import EvidenceWindow, PromotionEvidence
from thytrader.research.promotion_sources import STUDY_PAGE, every_study_of
from thytrader.risk.start_safeguards import LiveEvidenceThresholds
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentStatus,
    ExecutionStoreError,
    RuntimePhase,
)

if TYPE_CHECKING:
    from thytrader.research.catalog import ResearchStudyCatalog

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
    assert assessed.paper_days == "0.0"
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


def test_paper_days_are_compared_exactly_and_shown_rounded_down() -> None:
    """13 days 23 hours is short of 14 and shows as 13.9, never 14.0."""
    assessed = assess_live_start_evidence(
        _evidence(_window(40, "5")),
        paper_created_at=(_NOW - timedelta(days=13, hours=23),),
        thresholds=LiveEvidenceThresholds(),
        now=_NOW,
    )
    assert assessed.paper_days == "13.9"
    assert assessed.thin_reasons == ("13.9 days since its first paper book, fewer than 14",)


class _Catalog:
    """A study catalog double: strategy-filtered pages of summaries and their documents."""

    def __init__(self, documents: dict[str, str], *, broken: frozenset[str] = frozenset()) -> None:
        self.documents = documents
        self.broken = broken
        self.filters: list[object] = []

    async def list_summaries(
        self, *, kind: str | None = None, strategy_id: object = None, limit: int, offset: int
    ) -> tuple[object, ...]:
        del kind
        self.filters.append(strategy_id)
        names = sorted(self.documents)[offset : offset + limit]
        return tuple(SimpleNamespace(study_fingerprint=name) for name in names)

    async def load(self, study_fingerprint: str) -> str:
        if study_fingerprint in self.broken:
            raise StudyCatalogIntegrityError("Study failed integrity verification.")
        return self.documents[study_fingerprint]


class _FailingCatalog:
    """A catalog whose listing fails integrity verification."""

    async def list_summaries(self, **_kwargs: object) -> tuple[object, ...]:
        raise StudyCatalogIntegrityError("Malformed summary row.")


@pytest.mark.anyio
async def test_a_corrupt_catalog_is_unavailable_evidence_not_an_error() -> None:
    """An integrity failure anywhere in the catalog never stops a live start."""
    gathered = await gather_live_start_evidence(
        strategy_fingerprint=_FINGERPRINT,
        strategy_id=uuid4(),
        catalog=cast("ResearchStudyCatalog", _FailingCatalog()),
        execution=InMemoryExecutionStore(),
        thresholds=LiveEvidenceThresholds(),
        now=_NOW,
    )
    assert gathered.unavailable == ("studies",)
    assert "studies could not be read" in gathered.thin_reasons


@pytest.mark.anyio
async def test_an_unreadable_study_makes_the_research_incomplete() -> None:
    """A study that cannot be read could hold losses: the research is unavailable, not passed."""
    catalog = _Catalog({"s1": "{}", "s2": "{}"}, broken=frozenset({"s2"}))
    studies, complete = await every_study_of(
        cast("ResearchStudyCatalog", catalog),
        strategy_fingerprint=_FINGERPRINT,
        strategy_id=uuid4(),
    )
    assert studies == ()
    assert not complete


@pytest.mark.anyio
async def test_the_strategy_s_own_studies_are_read_page_by_page() -> None:
    """Studies are filtered to the strategy and paged, not the newest 100 of the catalog."""
    strategy_id = uuid4()
    catalog = _Catalog({f"s{index:03}": "{}" for index in range(STUDY_PAGE + 1)})
    _studies, complete = await every_study_of(
        cast("ResearchStudyCatalog", catalog),
        strategy_fingerprint=_FINGERPRINT,
        strategy_id=strategy_id,
    )
    assert not complete  # every "{}" document is invalid, so coverage is incomplete
    assert catalog.filters[0] == strategy_id


class _Slow(InMemoryExecutionStore):
    """An execution store whose strategy listing never returns in time."""

    async def list_by_strategy(self, strategy_id: str) -> tuple[Deployment, ...]:
        del strategy_id
        await asyncio.sleep(60)
        return ()


@pytest.mark.anyio
async def test_a_slow_source_runs_out_of_time_and_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gathering is bounded: a stalled source is reported, the start is not held."""
    monkeypatch.setattr(live_evidence_module, "EVIDENCE_READ_SECONDS", 0.05)
    gathered = await gather_live_start_evidence(
        strategy_fingerprint=_FINGERPRINT,
        strategy_id=uuid4(),
        catalog=None,
        execution=_Slow(),
        thresholds=LiveEvidenceThresholds(),
        now=_NOW,
    )
    assert gathered.unavailable == ("studies", "paper_books")


class _Malformed(InMemoryExecutionStore):
    """An execution store that fails while decoding a stored row."""

    async def list_by_strategy(self, strategy_id: str) -> tuple[Deployment, ...]:
        del strategy_id
        raise ValueError("invalid decimal in a stored row")


@pytest.mark.anyio
async def test_a_malformed_stored_row_is_unavailable_evidence() -> None:
    """Not only storage errors: any failure reading paper books makes them unavailable."""
    gathered = await gather_live_start_evidence(
        strategy_fingerprint=_FINGERPRINT,
        strategy_id=uuid4(),
        catalog=None,
        execution=_Malformed(),
        thresholds=LiveEvidenceThresholds(min_out_of_sample_trades=0),
        now=_NOW,
    )
    assert "paper_books" in gathered.unavailable
