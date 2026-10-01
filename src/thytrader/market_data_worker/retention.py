"""Worker-owned retention of superseded dataset revisions, plus a one-shot repair CLI.

The market-data worker is the only writer of the dataset volume (the API mounts it
read-only), so retention runs inside the worker loop between ingest cycles: once at
startup and then every ``interval``. Each pass is bounded and audited. The
``thytrader-market-data-retention`` command runs the same pass on demand inside the
worker container; it is a dry run unless ``--confirm`` is given.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
import logging
import sys
from typing import TYPE_CHECKING

from thytrader.market_data.dataset_retention import (
    DEFAULT_MAX_MANIFESTS_PER_PASS,
    DEFAULT_RETENTION_GRACE,
    DatasetReferenceSource,
    RetentionReport,
    collect_superseded_datasets,
)
from thytrader.market_data.datasets import DatasetStore
from thytrader.persistence.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
)
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_audit_events import PostgresAuditEventStore
from thytrader.persistence.postgres_dataset_references import PostgresDatasetReferenceSource
from thytrader.settings_yaml import SettingsStore

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from thytrader.persistence.audit_events import AuditEventStore

_logger = logging.getLogger(__name__)

DEFAULT_RETENTION_INTERVAL = timedelta(hours=6)


def _verifier(root: Path) -> Callable[[Path], bool]:
    """Return a full manifest-and-file verifier bound to one dataset root."""
    store = DatasetStore(root)

    def verify(manifest_path: Path) -> bool:
        store.load_verified(manifest_path)
        return True

    return verify


async def run_retention_pass(
    *,
    root: Path,
    references: DatasetReferenceSource,
    audit_store: AuditEventStore | None,
    now: datetime,
    dry_run: bool,
    grace: timedelta = DEFAULT_RETENTION_GRACE,
    max_manifests: int = DEFAULT_MAX_MANIFESTS_PER_PASS,
) -> RetentionReport:
    """Run one bounded pass, log its summary, and audit any deletion or abort."""
    report = await collect_superseded_datasets(
        root=root,
        references=references,
        verify=_verifier(root),
        now=now,
        dry_run=dry_run,
        grace=grace,
        max_manifests=max_manifests,
    )
    _logger.info("market_data_dataset_retention %s", report.summary())
    if audit_store is not None and (report.deleted_manifests or report.aborted is not None):
        await audit_store.append(
            AuditEvent(
                occurred_at=now,
                category=AuditEventCategory.MARKET_DATA,
                action="dataset_retention",
                outcome=(
                    AuditEventOutcome.FAILURE
                    if report.aborted is not None
                    else AuditEventOutcome.SUCCESS
                ),
                detail=report.summary()[:2048],
            )
        )
    return report


@dataclass(slots=True)
class DatasetRetentionRunner:
    """Schedule bounded retention passes from inside the single-writer worker loop."""

    root: Path
    references: DatasetReferenceSource
    audit_store: AuditEventStore | None = None
    interval: timedelta = DEFAULT_RETENTION_INTERVAL
    grace: timedelta = DEFAULT_RETENTION_GRACE
    max_manifests: int = DEFAULT_MAX_MANIFESTS_PER_PASS
    _next_due_at: datetime | None = field(default=None, init=False)

    async def maybe_run(self, now: datetime) -> RetentionReport | None:
        """Run a pass when due; a truncated pass is due again on the next cycle."""
        if self._next_due_at is not None and now < self._next_due_at:
            return None
        try:
            report = await run_retention_pass(
                root=self.root,
                references=self.references,
                audit_store=self.audit_store,
                now=now.astimezone(UTC),
                dry_run=False,
                grace=self.grace,
                max_manifests=self.max_manifests,
            )
        except Exception:  # noqa: BLE001 - retention must never stop ingestion.
            _logger.warning("market_data_dataset_retention_failed")
            self._next_due_at = now + self.interval
            return None
        self._next_due_at = now if report.truncated else now + self.interval
        return report


def _parser() -> argparse.ArgumentParser:
    """Build the one-shot retention CLI."""
    parser = argparse.ArgumentParser(
        prog="thytrader-market-data-retention",
        description=(
            "Delete superseded market-data dataset revisions that no stored record "
            "references. Keeps every referenced fingerprint, the newest revision, and the "
            "final revision of every island. Dry run unless --confirm is given. Run inside "
            "the market-data-worker container, which owns the dataset volume."
        ),
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Delete eligible revisions. Without it, only report what would be deleted.",
    )
    parser.add_argument(
        "--max-manifests",
        type=int,
        default=50_000,
        help="Upper bound on manifests deleted by this run (default 50000).",
    )
    parser.add_argument(
        "--grace-hours",
        type=int,
        default=int(DEFAULT_RETENTION_GRACE / timedelta(hours=1)),
        help="Keep revisions superseded more recently than this many hours (default 24).",
    )
    return parser


async def _run_cli(arguments: argparse.Namespace) -> RetentionReport:
    """Run one pass with the configured database and dataset root."""
    settings = SettingsStore.open().current()
    if settings.database_url is None:
        message = "Dataset retention requires THYTRADER_DATABASE_URL to read references."
        raise RuntimeError(message)
    engine = create_engine(settings.database_url)
    try:
        return await run_retention_pass(
            root=settings.market_data_dataset_root,
            references=PostgresDatasetReferenceSource(engine),
            audit_store=PostgresAuditEventStore(engine),
            now=datetime.now(UTC),
            dry_run=not arguments.confirm,
            grace=timedelta(hours=arguments.grace_hours),
            max_manifests=arguments.max_manifests,
        )
    finally:
        await dispose(engine)


def main(argv: Sequence[str] | None = None) -> None:
    """Run the one-shot retention pass and print its JSON report."""
    arguments = _parser().parse_args(argv)
    if arguments.max_manifests < 1 or arguments.grace_hours < 1:
        _parser().error("--max-manifests and --grace-hours must be positive.")
    report = asyncio.run(_run_cli(arguments))
    sys.stdout.write(json.dumps(report.as_payload(), indent=2, sort_keys=True) + "\n")
    if report.aborted is not None:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
