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


def test_schema_metadata_has_market_data_worker_state_table() -> None:
    """Operational metadata must define durable latest ingestion state separately."""
    table = metadata.tables["market_data_worker_state"]
    assert set(table.primary_key.columns.keys()) == {"provider", "product_id", "timeframe"}
    assert "last_attempt_at" in table.columns
    assert "last_success_at" in table.columns
    assert "covered_ends_at" in table.columns
    assert "content_fingerprint" in table.columns
    assert "failure_code" in table.columns
    assert "consecutive_failures" in table.columns
    assert "expected_ends_at" in table.columns
    assert "next_retry_at" in table.columns
    assert "dataset_revision" in table.columns
    assert "maintenance_kind" in table.columns


def test_market_data_worker_migration_follows_portfolio_history() -> None:
    """The second migration must add worker state without rewriting migration history."""
    content = Path("alembic/versions/0002_market_data_worker_state.py").read_text(encoding="utf-8")
    assert 'revision = "0002"' in content
    assert 'down_revision = "0001"' in content
    assert "market_data_worker_state" in content


def test_market_data_maintenance_migration_extends_worker_state() -> None:
    """The third migration must add continuous-maintenance coordination facts."""
    content = Path("alembic/versions/0003_market_data_maintenance.py").read_text(encoding="utf-8")
    assert 'revision = "0003"' in content
    assert 'down_revision = "0002"' in content
    assert "expected_ends_at" in content
    assert "next_retry_at" in content
    assert "dataset_revision" in content
    assert "maintenance_kind" in content


def test_market_data_revision_backfill_follows_maintenance_migration() -> None:
    """The fourth migration assigns revision one to legacy verified datasets."""
    content = Path("alembic/versions/0004_market_data_revision_backfill.py").read_text(
        encoding="utf-8"
    )
    assert 'revision = "0004"' in content
    assert 'down_revision = "0003"' in content
    assert "UPDATE market_data_worker_state" in content
    assert "dataset_revision = 1" in content


def test_schema_metadata_has_immutable_strategy_publication_tables() -> None:
    """Published definitions and exact dataset bindings have separate durable identities."""
    strategies = metadata.tables["published_strategy_versions"]
    bindings = metadata.tables["strategy_dataset_bindings"]

    assert set(strategies.primary_key.columns.keys()) == {"strategy_fingerprint"}
    assert "canonical_definition" in strategies.columns
    assert set(bindings.primary_key.columns.keys()) == {
        "strategy_fingerprint",
        "dataset_fingerprint",
    }
    strategy_constraints = {constraint.name for constraint in strategies.constraints}
    binding_constraints = {constraint.name for constraint in bindings.constraints}
    assert "ck_published_strategy_version_positive" in strategy_constraints
    assert "ck_published_strategy_fingerprint_format" in strategy_constraints
    assert "ck_strategy_dataset_binding_strategy_fingerprint_format" in binding_constraints
    assert "ck_strategy_dataset_binding_dataset_fingerprint_format" in binding_constraints


def test_strategy_publication_migration_follows_market_data_backfill() -> None:
    """The fifth migration adds immutable strategy publication without rewriting history."""
    content = Path("alembic/versions/0005_published_strategy_versions.py").read_text(
        encoding="utf-8"
    )
    assert 'revision = "0005"' in content
    assert 'down_revision = "0004"' in content
    assert "published_strategy_versions" in content
    assert "strategy_dataset_bindings" in content
    assert "ck_published_strategy_version_positive" in content
    assert "ck_published_strategy_fingerprint_format" in content
    assert "ck_strategy_dataset_binding_dataset_fingerprint_format" in content


def test_schema_metadata_has_immutable_research_run_specifications() -> None:
    """Research requests must retain exact artifact identities and canonical content."""
    table = metadata.tables["published_research_run_specs"]

    assert set(table.primary_key.columns.keys()) == {"run_fingerprint"}
    assert "run_id" in table.columns
    assert "strategy_fingerprint" in table.columns
    assert "dataset_fingerprint" in table.columns
    assert "canonical_specification" in table.columns
    constraints = {constraint.name for constraint in table.constraints}
    assert "ck_research_run_fingerprint_format" in constraints
    assert "ck_research_run_strategy_fingerprint_format" in constraints
    assert "ck_research_run_dataset_fingerprint_format" in constraints


def test_research_run_specification_migration_follows_strategy_publication() -> None:
    """The sixth migration must append immutable run specifications without rewriting history."""
    content = Path("alembic/versions/0006_published_research_run_specs.py").read_text(
        encoding="utf-8"
    )

    assert 'revision = "0006"' in content
    assert 'down_revision = "0005"' in content
    assert "published_research_run_specs" in content
    assert "canonical_specification" in content
    assert "ck_research_run_fingerprint_format" in content
    assert "ck_research_run_strategy_fingerprint_format" in content
    assert "ck_research_run_dataset_fingerprint_format" in content


def test_audit_events_migration_follows_strategy_drafts() -> None:
    """The tenth migration must append audit events without mutating historical tables."""
    content = Path("alembic/versions/0010_audit_events.py").read_text(encoding="utf-8")
    assert 'revision = "0010"' in content
    assert 'down_revision = "0009"' in content
    assert "audit_events" in content
    assert "ck_audit_events_category" in content
    assert "ck_audit_events_outcome" in content
    assert "ix_audit_events_occurred_at_desc" in content


def test_market_feed_state_migration_follows_audit_events() -> None:
    """The eleventh migration must append ticker snapshots without rewriting history."""
    content = Path("alembic/versions/0011_market_feed_state.py").read_text(encoding="utf-8")
    assert 'revision = "0011"' in content
    assert 'down_revision = "0010"' in content
    assert "market_feed_state" in content
    assert "ck_market_feed_state_value" in content


def test_execution_runtime_migration_follows_market_feed_state() -> None:
    """The twelfth migration must add paper/live execution tables without rewriting history."""
    content = Path("alembic/versions/0012_execution_runtime.py").read_text(encoding="utf-8")
    assert 'revision = "0012"' in content
    assert 'down_revision = "0011"' in content
    assert "deployments" in content
    assert "order_intents" in content
    assert "execution_orders" in content
    assert "execution_fills" in content
    assert "execution_positions" in content
    assert "ck_deployments_mode" in content
    assert "ux_order_intents_client_order_id" in content


def test_audit_research_category_migration_follows_execution_runtime() -> None:
    """The thirteenth migration must allow research audit events without rewriting history."""
    content = Path("alembic/versions/0013_audit_research_category.py").read_text(encoding="utf-8")
    assert 'revision = "0013"' in content
    assert 'down_revision = "0012"' in content
    assert "research" in content
    assert "ck_audit_events_category" in content


def test_audit_runtime_category_migration_follows_research() -> None:
    """The fourteenth migration must allow runtime-control audit events."""
    content = Path("alembic/versions/0014_audit_runtime_category.py").read_text(encoding="utf-8")
    assert 'revision = "0014"' in content
    assert 'down_revision = "0013"' in content
    assert "runtime" in content
    assert "ck_audit_events_category" in content


def test_watchlist_fifteen_minute_migration_follows_ingest_jobs() -> None:
    """The seventeenth migration must widen watchlist timeframes to 15m datasets."""
    content = Path("alembic/versions/0017_watchlist_fifteen_minute_datasets.py").read_text(
        encoding="utf-8"
    )
    assert 'revision = "0017"' in content
    assert 'down_revision = "0016"' in content
    assert "15m" in content
    assert "ck_market_data_watchlist_timeframe" in content


def test_watchlist_thirty_minute_migration_follows_fifteen_minute() -> None:
    """The eighteenth migration must widen watchlist timeframes to 30m datasets."""
    content = Path("alembic/versions/0018_watchlist_thirty_minute_datasets.py").read_text(
        encoding="utf-8"
    )
    assert 'revision = "0018"' in content
    assert 'down_revision = "0017"' in content
    assert "30m" in content
    assert "ck_market_data_watchlist_timeframe" in content


def test_watchlist_six_hour_migration_follows_thirty_minute() -> None:
    """The nineteenth migration must widen watchlist timeframes to 6h datasets."""
    content = Path("alembic/versions/0019_watchlist_six_hour_datasets.py").read_text(
        encoding="utf-8"
    )
    assert 'revision = "0019"' in content
    assert 'down_revision = "0018"' in content
    assert "6h" in content
    assert "ck_market_data_watchlist_timeframe" in content


def test_watchlist_one_day_migration_follows_six_hour() -> None:
    """The twentieth migration must widen watchlist timeframes to 1d datasets."""
    content = Path("alembic/versions/0020_watchlist_one_day_datasets.py").read_text(
        encoding="utf-8"
    )
    assert 'revision = "0020"' in content
    assert 'down_revision = "0019"' in content
    assert "1d" in content
    assert "ck_market_data_watchlist_timeframe" in content


def test_risk_policy_migration_follows_one_day_watchlist() -> None:
    """The twenty-first migration must add published and active risk-policy tables."""
    content = Path("alembic/versions/0021_risk_policy_registry.py").read_text(encoding="utf-8")
    assert 'revision = "0021"' in content
    assert 'down_revision = "0020"' in content
    assert "published_risk_policies" in content
    assert "active_risk_policy" in content
    assert "ck_active_risk_policy_singleton" in content


def test_phase13_live_extras_migration_follows_risk_policy() -> None:
    """The twenty-second migration must add trailing, OCO trigger, and user-feed state."""
    content = Path("alembic/versions/0022_phase13_live_extras.py").read_text(encoding="utf-8")
    assert 'revision = "0022"' in content
    assert 'down_revision = "0021"' in content
    assert "trail_extreme" in content
    assert "stop_trigger_price" in content
    assert "trigger_bracket" in content
    assert "user_order_feed_state" in content
    assert "ck_user_order_feed_state_singleton" in content


def test_experiential_memory_migration_follows_live_extras() -> None:
    """The twenty-third migration must add experiential-memory tables."""
    content = Path("alembic/versions/0023_experiential_memory.py").read_text(encoding="utf-8")
    assert 'revision = "0023"' in content
    assert 'down_revision = "0022"' in content
    assert "experiential_journal_entries" in content
    assert "experiential_notifications" in content
    assert "'memory'" in content


def test_watchlist_remaining_coinbase_granularity_migration_follows_memory() -> None:
    """The twenty-fourth migration must widen watchlist timeframes to 1m, 2h, and 4h."""
    migration = Path("alembic/versions/0024_watchlist_one_minute_two_hour_four_hour_datasets.py")
    content = migration.read_text(encoding="utf-8")
    assert 'revision = "0024"' in content
    assert 'down_revision = "0023"' in content
    assert "1m" in content
    assert "2h" in content
    assert "4h" in content
    assert "ck_market_data_watchlist_timeframe" in content


def test_on_demand_discretionary_migration_follows_datasets() -> None:
    """The twenty-fifth migration must add discretionary books after dataset 0024."""
    content = Path("alembic/versions/0025_on_demand_discretionary.py").read_text(encoding="utf-8")
    assert 'revision = "0025"' in content
    assert 'down_revision = "0024"' in content
    assert "kind" in content
    assert "idempotency_key" in content
    assert "discretionary" in content


def test_venue_execution_clocks_migration_follows_discretionary() -> None:
    """The twenty-sixth migration must widen deployment clocks after discretionary 0025."""
    content = Path("alembic/versions/0026_venue_execution_clocks.py").read_text(encoding="utf-8")
    assert 'revision = "0026"' in content
    assert 'down_revision = "0025"' in content
    assert "1m" in content
    assert "2h" in content
    assert "4h" in content
    assert "ck_deployments_timeframe" in content
    assert "ck_deployments_kind_identity" in content


def test_spot_shorting_migration_follows_venue_clocks() -> None:
    """The twenty-seventh migration must add position side and attached take-profit."""
    content = Path("alembic/versions/0027_spot_shorting_attached_brackets.py").read_text(
        encoding="utf-8"
    )
    assert 'revision = "0027"' in content
    assert 'down_revision = "0026"' in content
    assert "take_profit_price" in content
    assert "ck_execution_positions_side" in content
    assert "short" in content


def test_paper_deploy_fee_migration_follows_spot_shorting() -> None:
    """The twenty-eighth migration must persist paper maker/taker assumptions after 0027."""
    content = Path("alembic/versions/0028_paper_deploy_fee_rates.py").read_text(encoding="utf-8")
    assert 'revision = "0028"' in content
    assert 'down_revision = "0027"' in content
    assert "paper_maker_fee_rate" in content
    assert "paper_taker_fee_rate" in content
    assert "ck_deployments_paper_fee_rates" in content
    assert "0.001" in content
    assert "0.002" in content


def test_experiential_models_migration_follows_paper_deploy_fees() -> None:
    """The twenty-ninth migration must add trained experiential models only."""
    content = Path("alembic/versions/0029_experiential_models.py").read_text(encoding="utf-8")
    assert 'revision = "0029"' in content
    assert 'down_revision = "0028"' in content
    assert "experiential_models" in content
    assert "uq_experiential_models_fingerprint" in content
    assert "thytrader-experiential-train-v1" in content
    assert "experiential_journal_entries" not in content


def test_risk_breaker_overlay_migration_follows_experiential_models() -> None:
    """The thirtieth migration must follow 0029 without rewriting stored policy JSON."""
    content = Path("alembic/versions/0030_risk_breaker_overlay.py").read_text(encoding="utf-8")
    assert 'revision = "0030"' in content
    assert 'down_revision = "0029"' in content
    assert "published_risk_policies" in content
    assert "ADR 0050" in content
    assert "canonical" in content
    assert "COMMENT ON TABLE" in content


def test_research_study_catalog_migration_follows_risk_breaker_overlay() -> None:
    """The thirty-first migration must persist composed research-study rows after 0030."""
    content = Path("alembic/versions/0031_research_study_catalog.py").read_text(encoding="utf-8")
    assert 'revision = "0031"' in content
    assert 'down_revision = "0030"' in content
    assert "published_research_studies" in content
    assert "ck_research_study_kind" in content
    assert "parameter_sweep" in content


def test_trade_reason_journals_migration_follows_study_catalog() -> None:
    """The thirty-second migration must add why-trade rows after the study catalog."""
    content = Path("alembic/versions/0032_trade_reason_journals.py").read_text(encoding="utf-8")
    assert 'revision = "0032"' in content
    assert 'down_revision = "0031"' in content
    assert "trade_reason_records" in content
    assert "ux_trade_reason_records_intent_id" in content
    assert "experiential_models" not in content
    assert "published_research_studies" not in content


def test_multi_instrument_pyramiding_migration_follows_trade_reason_journals() -> None:
    """The thirty-third migration must key positions by product after why-trade journals."""
    content = Path("alembic/versions/0033_multi_instrument_pyramiding.py").read_text(
        encoding="utf-8"
    )
    assert 'revision = "0033"' in content
    assert 'down_revision = "0032"' in content
    assert "execution_instrument_state" in content
    assert "pk_execution_positions" in content
    assert "add_count" in content


def test_protection_lifecycle_accounting_migration_follows_atomic_fill_ledger() -> None:
    """The thirty-fifth migration must add lifecycle and capital columns after 0034."""
    content = Path("alembic/versions/0035_protection_lifecycle_accounting.py").read_text(
        encoding="utf-8"
    )
    assert 'revision = "0035"' in content
    assert 'down_revision = "0034"' in content
    assert "lifecycle_command" in content
    assert "ux_deployments_active_strategy_mode" in content
    assert "utc_day_open_equity" in content
    assert "high_water_mark_equity" in content


def test_research_ops_contract_v25_migration_follows_portfolio_slice() -> None:
    """The thirty-eighth migration must mark ops-contract v25 after 0037."""
    content = Path("alembic/versions/0038_research_ops_contract_v25.py").read_text(encoding="utf-8")
    assert 'revision = "0038"' in content
    assert 'down_revision = "0037"' in content
    assert "ADR 0066" in content
    assert "thytrader-bar-backtest-v4" in content


def test_operator_ops_contract_v26_migration_follows_research_slice() -> None:
    """The thirty-ninth migration must mark ops-contract v26 after 0038."""
    content = Path("alembic/versions/0039_ops_contract_v26_breaker_latch_reset.py").read_text(
        encoding="utf-8"
    )
    assert 'revision = "0039"' in content
    assert 'down_revision = "0038"' in content
    assert "ADR 0064" in content
    assert "breaker latch reset" in content.lower()


def test_research_ops_contract_v27_migration_follows_operator_slice() -> None:
    """The fortieth migration must mark ops-contract v27 after 0039."""
    content = Path("alembic/versions/0040_research_ops_contract_v27.py").read_text(encoding="utf-8")
    assert 'revision = "0040"' in content
    assert 'down_revision = "0039"' in content
    assert "ADR 0069" in content
    assert "async backtest" in content.lower()


def test_extended_slow_timeframe_lookback_migration_follows_research_slice() -> None:
    """The forty-first migration widens slow-timeframe watch lookback after 0040."""
    content = Path("alembic/versions/0041_extended_slow_timeframe_lookback.py").read_text(
        encoding="utf-8"
    )
    assert 'revision = "0041"' in content
    assert 'down_revision = "0040"' in content
    assert "8760" in content


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
    assert "127.0.0.1:5439:5432" in content
    assert "POSTGRES_PASSWORD" in content


def test_compose_yaml_defines_a_migration_gated_full_stack() -> None:
    """Compose must start API, worker, and web only after safe prerequisites."""
    content = Path("compose.yaml").read_text(encoding="utf-8")
    api_block = content.split("  api:", maxsplit=1)[1].split("  worker:", maxsplit=1)[0]

    assert "  migrate:" in content
    assert "  api:" in content
    assert "  worker:" in content
    assert "  market-data-worker:" in content
    assert "  execution-worker:" in content
    assert "  web:" in content
    assert "condition: service_healthy" in content
    assert "thytrader_market_data:" in content
    assert "THYTRADER_MARKET_DATA_DATASET_ROOT: /var/lib/thytrader/market-data" in content
    assert "THYTRADER_MARKET_DATA_DATASET_ROOT: /var/lib/thytrader/market-data" in api_block
    assert "- thytrader_market_data:/var/lib/thytrader/market-data:ro" in api_block
    assert "condition: service_completed_successfully" in content
    assert "THYTRADER_API_PORT: ${THYTRADER_API_PORT:-8200}" in content
    assert "127.0.0.1:${THYTRADER_API_PORT:-8200}:${THYTRADER_API_PORT:-8200}" in content
    assert "os.environ['THYTRADER_API_PORT']" in content
    assert "THYTRADER_API_PROXY_TARGET: http://api:${THYTRADER_API_PORT:-8200}" in content
    assert "127.0.0.1:5175:5175" in content
    assert "THYTRADER_SETTINGS_FILE: /var/lib/thytrader/settings/thytrader.yaml" in content
    assert "./thytrader.yaml:/var/lib/thytrader/settings/thytrader.yaml" in content
    assert "THYTRADER_YOLO_TIERS" not in content
    assert "THYTRADER_SNAPSHOT_INTERVAL_SECONDS" not in content
