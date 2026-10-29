"""Command-line entry point for the strategy execution worker."""

from __future__ import annotations

import asyncio
import contextlib
from functools import partial
import logging
import os
from pathlib import Path
import signal

from thytrader.alerts.service import AlertService
from thytrader.alerts.supervision_inputs import AlertThresholds
from thytrader.credentials.worker_runtime import WorkerCredentialRuntime
from thytrader.execution.futures_paper import (
    FuturesRuntime,
    futures_runtime_scope,
    margin_rate_max_age,
)
from thytrader.execution.paper import PaperBroker
from thytrader.execution_worker.futures_venue import coinbase_futures_extension
from thytrader.execution_worker.service import run_execution_worker
from thytrader.execution_worker.venue import ExecutionVenueRuntime, build_execution_venue
from thytrader.execution_worker.venue_feed import run_venue_user_order_feed
from thytrader.memory.notify import ReloadingNotificationSender
from thytrader.observability.logging import configure_logging
from thytrader.persistence.database import create_engine, dispose, ping
from thytrader.persistence.postgres_alerts import PostgresAlertStore
from thytrader.persistence.postgres_audit_events import PostgresAuditEventStore
from thytrader.persistence.postgres_decisions import PostgresDecisionJournalStore
from thytrader.persistence.postgres_desks import PostgresDeskStore
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.postgres_execution_cycles import PostgresExecutionCycleStore
from thytrader.persistence.postgres_futures import PostgresFuturesObservationStore
from thytrader.persistence.postgres_futures_account import PostgresFuturesAccountStore
from thytrader.persistence.postgres_futures_books import PostgresFuturesContractStore
from thytrader.persistence.postgres_memory import PostgresExperientialMemoryStore
from thytrader.persistence.postgres_portfolios import PostgresPortfolioStore
from thytrader.persistence.postgres_risk import PostgresRiskPolicyStore
from thytrader.persistence.postgres_strategies import PostgresStrategyStore
from thytrader.persistence.postgres_user_feed import PostgresUserOrderFeedStateStore
from thytrader.persistence.postgres_worker_heartbeats import PostgresWorkerHeartbeatStore
from thytrader.persistence.query_timing import instrument_engine
from thytrader.replay.capture import FileCapture, capture_scope
from thytrader.risk.futures_collateral import risk_futures_account_scope
from thytrader.settings_yaml import SettingsStore

_logger = logging.getLogger(__name__)


async def run() -> None:
    """Run execution until an operating-system shutdown signal arrives."""
    settings_store = SettingsStore.open()
    settings = settings_store.current()
    configure_logging(settings)
    if settings.database_url is None:
        message = "The execution worker requires THYTRADER_DATABASE_URL for durable state."
        raise RuntimeError(message)

    engine = create_engine(settings.database_url)
    # Every statement this worker runs is counted and timed per cycle (ADR 0131).
    instrument_engine(engine)
    store = PostgresExecutionStore(engine)
    publication_store = PostgresStrategyStore(engine)
    risk_store = PostgresRiskPolicyStore(engine)
    memory_store = PostgresExperientialMemoryStore(engine)
    heartbeats = PostgresWorkerHeartbeatStore(engine)
    audit_store = PostgresAuditEventStore(engine)
    decision_store = PostgresDecisionJournalStore(engine)
    portfolio_store = PostgresPortfolioStore(engine)
    alert_service = AlertService(
        PostgresAlertStore(engine),
        ReloadingNotificationSender(settings_store),
        thresholds=AlertThresholds(
            consecutive_failure_cycles=settings.alert_consecutive_failure_cycles,
            decision_missed_bars=settings.alert_decision_missed_bars,
            delivery_max_attempts=settings.alert_delivery_max_attempts,
            entry_block_grace_seconds=settings.alert_entry_block_grace_seconds,
        ),
        min_severity=lambda: settings_store.current().notify_min_severity,
    )
    futures_observations = PostgresFuturesObservationStore(engine)
    futures_accounts = PostgresFuturesAccountStore(engine)
    # Live futures books get a product-routed broker on the generation's REST client; spot
    # books keep the plain spot broker (ADR 0134 P2-7).
    venue_runtime = ExecutionVenueRuntime(
        settings,
        builder=partial(
            build_execution_venue,
            extend=coinbase_futures_extension(
                observations=futures_observations, mirror=futures_accounts
            ),
        ),
    )
    credential_runtime = WorkerCredentialRuntime(
        settings_store,
        on_coinbase_reload=venue_runtime.replace,
    )
    try:
        try:
            await ping(engine)
        except Exception as error:  # noqa: BLE001 - startup boundary redacts database details.
            _logger.warning("execution_worker_database_unavailable type=%s", type(error).__name__)
            message = "The execution worker could not connect to its durable state store."
            raise RuntimeError(message) from None

        stop_requested = asyncio.Event()
        wake_requested = asyncio.Event()
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, stop_requested.set)
        loop.add_signal_handler(signal.SIGTERM, stop_requested.set)
        _logger.info("execution_worker_started")
        user_feed_store = PostgresUserOrderFeedStateStore(engine)
        initial = venue_runtime.current()
        # Live entry admission reads the CFM mirror for the shared-collateral gate, and
        # paper futures books read their bound contract, margin and funding (ADR 0129);
        # tasks created below inherit these bindings.
        futures_runtime = FuturesRuntime(
            contracts=PostgresFuturesContractStore(engine),
            observations=futures_observations,
            margin_max_age=margin_rate_max_age(settings),
        )
        with (
            risk_futures_account_scope(futures_accounts),
            futures_runtime_scope(futures_runtime),
            capture_scope(configured_replay_capture()),
        ):
            await asyncio.gather(
                run_execution_worker(
                    stop_requested,
                    store=store,
                    publication_store=publication_store,
                    market_data=initial.market_data,
                    paper_broker=PaperBroker(),
                    live_broker=initial.live_broker,
                    quote_reader=initial.quote_reader,
                    interval_seconds=settings.execution_worker_interval_seconds,
                    on_readiness_changed=lambda ready: _set_readiness(
                        settings.execution_worker_readiness_file, ready
                    ),
                    heartbeat_store=heartbeats,
                    risk_store=risk_store,
                    user_feed_store=user_feed_store,
                    wake_requested=wake_requested,
                    memory_store=memory_store,
                    settings_store=settings_store,
                    venue_provider=venue_runtime.current,
                    audit_store=audit_store,
                    decision_store=decision_store,
                    portfolio_store=portfolio_store,
                    alert_service=alert_service,
                    cycle_store=PostgresExecutionCycleStore(engine),
                    desk_store=PostgresDeskStore(engine),
                ),
                run_venue_user_order_feed(
                    stop_requested,
                    venue_provider=venue_runtime.current,
                    feed_store=user_feed_store,
                    audit_store=audit_store,
                    wake_requested=wake_requested,
                ),
                credential_runtime.run_until_stopped(stop_requested),
                alert_service.run_deliveries(stop_requested),
            )
        _logger.info("execution_worker_stopped")
    finally:
        _set_readiness(settings.execution_worker_readiness_file, False)
        await dispose(engine)


def _set_readiness(readiness_file: Path | None, ready: bool) -> None:
    """Synchronize the supervisor-facing readiness marker."""
    if readiness_file is None:
        return
    if ready:
        readiness_file.parent.mkdir(parents=True, exist_ok=True)
        readiness_file.touch()
        return
    with contextlib.suppress(FileNotFoundError):
        readiness_file.unlink()


def configured_replay_capture() -> FileCapture | None:
    """Resolve the explicit contributor opt-in; never read credentials or create directories."""
    root = os.environ.get("THYTRADER_REPLAY_CAPTURE_ROOT")
    return FileCapture(Path(root)) if root else None


def main() -> None:
    """Start the dedicated execution worker process."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
