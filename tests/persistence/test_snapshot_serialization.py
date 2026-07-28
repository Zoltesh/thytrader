"""Unit tests for PostgreSQL snapshot serialization and migration metadata."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

from thytrader.persistence.portfolio_history import (
    DisabledPortfolioHistoryStore,
    InMemoryPortfolioHistoryStore,
    PortfolioHistoryEntry,
    PortfolioHistoryUnavailableError,
)
from thytrader.persistence.postgres_history import _portfolio_to_snapshot
from thytrader.persistence.schema import metadata
from thytrader.portfolio.models import (
    Money,
    Portfolio,
    PortfolioAsset,
    PortfolioConnection,
)


def _sample_portfolio() -> Portfolio:
    """Return a deterministic demo portfolio with known exact decimals."""
    return Portfolio(
        as_of=datetime(2026, 7, 27, 20, 0, 0, tzinfo=UTC),
        connection=PortfolioConnection(
            provider="coinbase",
            status="demo",
            permissions=("read",),
        ),
        demo=True,
        total_value=Money(amount=Decimal("31415.926535"), currency="USD"),
        assets=(
            PortfolioAsset(
                currency="BTC",
                name="Bitcoin",
                available=Decimal("0.5"),
                hold=Decimal("0"),
                total=Decimal("0.5"),
                value=Money(amount=Decimal("31415.926535"), currency="USD"),
            ),
        ),
        unvalued_assets=(),
    )


def test_disabled_store_record_is_noop() -> None:
    """Disabled persistence must not raise on record."""
    store = DisabledPortfolioHistoryStore()
    asyncio.run(store.record(_sample_portfolio()))


def test_disabled_store_list_raises_typed_error() -> None:
    """Disabled persistence must not be indistinguishable from empty history."""
    store = DisabledPortfolioHistoryStore()
    try:
        asyncio.run(store.list_range(start=None, max_entries=5))
    except PortfolioHistoryUnavailableError:
        return
    raise AssertionError("Expected PortfolioHistoryUnavailableError")


def test_in_memory_store_samples_a_time_range_without_losing_endpoints() -> None:
    """Presentation sampling preserves the oldest and newest range observations."""
    store = InMemoryPortfolioHistoryStore()
    start = datetime(2026, 7, 27, 0, 0, tzinfo=UTC)
    store._entries = [
        PortfolioHistoryEntry(as_of=start - timedelta(hours=1), total_value=Decimal("90")),
        PortfolioHistoryEntry(as_of=start, total_value=Decimal("100")),
        PortfolioHistoryEntry(as_of=start.replace(hour=1), total_value=Decimal("110")),
        PortfolioHistoryEntry(as_of=start.replace(hour=2), total_value=Decimal("120")),
        PortfolioHistoryEntry(as_of=start.replace(hour=3), total_value=Decimal("130")),
        PortfolioHistoryEntry(as_of=start.replace(hour=4), total_value=Decimal("140")),
    ]

    entries = asyncio.run(store.list_range(start=start, max_entries=3))

    assert [entry.total_value for entry in entries] == [
        Decimal("140"),
        Decimal("120"),
        Decimal("100"),
    ]


def test_snapshot_preserves_exact_decimal_strings() -> None:
    """The JSON snapshot must render all Decimals as exact strings."""
    snapshot = _portfolio_to_snapshot(_sample_portfolio())
    total_value = cast("dict[str, str]", snapshot["total_value"])
    assert total_value["amount"] == "31415.926535"
    assets = cast("list[dict[str, object]]", snapshot["assets"])
    asset = assets[0]
    assert asset["available"] == "0.5"
    assert asset["hold"] == "0"
    asset_value = cast("dict[str, str]", asset["value"])
    assert asset_value["amount"] == "31415.926535"
    connection = cast("dict[str, object]", snapshot["connection"])
    assert connection["permissions"] == ["read"]


def test_schema_metadata_has_portfolio_snapshots_table() -> None:
    """Core metadata must contain the append-only snapshots table."""
    assert "portfolio_snapshots" in metadata.tables
    table = metadata.tables["portfolio_snapshots"]
    assert "id" in table.columns
    assert "as_of" in table.columns
    assert "total_usd_value" in table.columns
    assert "snapshot" in table.columns


def test_migration_file_exists_with_correct_revision() -> None:
    """The initial migration must exist and declare revision 0001."""
    migration_path = Path("alembic/versions/0001_portfolio_snapshots.py")
    assert migration_path.exists()
    content = migration_path.read_text(encoding="utf-8")
    assert 'revision = "0001"' in content
    assert "down_revision = None" in content
    assert "def upgrade" in content
    assert "def downgrade" in content
    assert "portfolio_snapshots" in content


def test_migration_upgrade_emits_create_table() -> None:
    """Offline migration upgrade must create the portfolio_snapshots table."""
    migration_path = Path("alembic/versions/0001_portfolio_snapshots.py")
    content = migration_path.read_text(encoding="utf-8")
    assert "create_table" in content
    assert "portfolio_snapshots" in content
    assert "ix_portfolio_snapshots_as_of_desc" in content
    assert "drop_table" in content


def test_compose_yaml_binds_postgres_to_loopback_only() -> None:
    """The compose file must not expose PostgreSQL beyond loopback."""
    compose_path = Path("compose.yaml")
    assert compose_path.exists()
    content = compose_path.read_text(encoding="utf-8")
    assert "127.0.0.1:5433:5432" in content
    assert "POSTGRES_PASSWORD" in content


def test_compose_yaml_defines_a_migration_gated_full_stack() -> None:
    """Compose must start API, worker, and web only after safe prerequisites."""
    content = Path("compose.yaml").read_text(encoding="utf-8")

    assert "  migrate:" in content
    assert "  api:" in content
    assert "  worker:" in content
    assert "  web:" in content
    assert "condition: service_healthy" in content
    assert "condition: service_completed_successfully" in content
    assert "127.0.0.1:8200:8200" in content
    assert "127.0.0.1:5175:5175" in content
    assert "THYTRADER_WORKER_READINESS_FILE" in content
