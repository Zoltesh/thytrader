"""Tests for confirmation-gated research mutations over the mutable strategy store."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from thytrader.audit_events import AuditEventCategory, InMemoryAuditEventStore
from thytrader.backtest.submission import BacktestStartRequest, BacktestSubmissionResult
from thytrader.persistence.backtest_results import DisabledBacktestResultStore
from thytrader.research.mutation import ResearchMutator
from thytrader.strategies.library import StrategyInvalidError
from thytrader.strategies.memory_store import InMemoryStrategyStore
from thytrader.strategies.models import strategy_fingerprint

if TYPE_CHECKING:
    from thytrader.backtest.submission import BacktestSubmissionRequest


class _RecordingSubmitter:
    """Record submitted fingerprints and return fixed identities."""

    def __init__(self) -> None:
        """Start with no submissions."""
        self.fingerprints: list[str] = []

    async def submit(self, request: BacktestSubmissionRequest) -> BacktestSubmissionResult:
        """Record the snapshot the mutator bound."""
        self.fingerprints.append(request.strategy_fingerprint)
        return BacktestSubmissionResult(
            run_fingerprint="sha256:" + "a" * 64,
            result_fingerprint="sha256:" + "b" * 64,
        )


def _mutator() -> tuple[ResearchMutator, InMemoryStrategyStore, InMemoryAuditEventStore]:
    """Build one mutator over in-memory stores."""
    store = InMemoryStrategyStore()
    audit = InMemoryAuditEventStore()
    mutator = ResearchMutator(
        strategies=store,
        publications=store,
        submitter=_RecordingSubmitter(),
        results=DisabledBacktestResultStore(),
        audit=audit,
    )
    return mutator, store, audit


def test_create_save_import_clone_and_delete_record_research_audit() -> None:
    """Every strategy mutation lands in the research audit trail."""

    async def exercise() -> None:
        mutator, store, audit = _mutator()
        created = await mutator.create_strategy(product_id="ETH-USD", timeframe="4h")
        assert created.validation.valid
        assert created.product_id == "ETH-USD"
        document = dict(created.document)
        document["name"] = "Renamed"
        saved = await mutator.save_strategy(created.strategy_id, document, expected_revision=1)
        assert saved.revision == 2
        imported = await mutator.import_strategy(created.document)
        assert imported.strategy_id != created.strategy_id
        cloned = await mutator.clone_strategy(created.strategy_id)
        assert cloned.name == "Renamed (copy)"
        report = await mutator.delete_strategies(
            (created.strategy_id, imported.strategy_id), dry_run=False
        )
        assert [item.outcome for item in report.items] == ["deleted", "deleted"]
        assert (await store.list_page(limit=10, offset=0)).total == 1
        events = await audit.list_recent(limit=20)
        actions = [event.action for event in events]
        assert {"create_strategy", "save_strategy", "import_strategy", "clone_strategy"} <= set(
            actions
        )
        assert actions.count("delete_strategy") == 2
        assert all(event.category is AuditEventCategory.RESEARCH for event in events)

    asyncio.run(exercise())


def test_start_backtest_snapshots_the_current_definition() -> None:
    """Local backtests bind the current snapshot; an invalid document fails closed."""

    async def exercise() -> None:
        mutator, store, _audit = _mutator()
        created = await mutator.create_strategy()
        assert created.definition is not None
        start = BacktestStartRequest.model_validate(
            {
                "strategy_id": str(created.strategy_id),
                "dataset_fingerprint": "sha256:" + "d" * 64,
                "initial_quote_balance": "10000",
                "maker_fee_rate": "0.001",
                "taker_fee_rate": "0.002",
                "fixed_slippage_bps": "1",
            }
        )
        _run, _result, fingerprint, bound = await mutator.start_backtest(start)
        assert fingerprint == strategy_fingerprint(created.definition)
        assert [(item.dataset_fingerprint, item.source) for item in bound] == [
            ("sha256:" + "d" * 64, "request")
        ]
        broken = dict(created.document)
        broken["indicators"] = []
        await store.save(created.strategy_id, broken, expected_revision=1)
        try:
            await mutator.start_backtest(start)
        except StrategyInvalidError:
            pass
        else:
            raise AssertionError("an invalid definition must not start a backtest")

    asyncio.run(exercise())
