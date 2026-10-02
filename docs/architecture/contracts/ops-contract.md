# Ops contract

CLI versus running-image **content identity**. Package version `0.1.0` is not
current-image evidence. Model: `thytrader.operator.models.OpsContractPayload`.
Compiled expected payload: `thytrader.ops_contract.expected_ops_contract()`.

Every HTTP agent CLI preflights `GET /health/ready` and fails closed on a
missing or unequal contract. Rebuild with `make run`. Do not default-fill a
missing payload.

Current checkout (Alembic `0054`):

| Field | Shipped value |
|---|---|
| `id` | `thytrader-ops-contract-v48` |
| `expected_schema_revision` | `0054` |
| `strategy_model` | `mutable_root`, `auto_snapshot`, `hard_delete` ([ADR 0082](../../decisions/0082-strategy-root-mutable-strategies-auto-snapshots.md)) |
| `portfolio_model` | `sleeves`, `shared_limits`, `manager_settings`, `journal`, `portfolio_backtest` ([ADR 0088](../../decisions/0088-portfolio-model-and-portfolio-backtest.md)) |
| `portfolio_modes` | `paper`, `live` |
| `portfolio_backtest_contract` | `thytrader-portfolio-backtest-v1` |
| `max_concurrent_portfolio_backtests` | `1` |
| `async_backtest_job_statuses` | `queued`, `running`, `completed`, `failed`, `cancelled`, `expired` |
| `research_job_statuses` | same as `async_backtest_job_statuses` |
| `max_concurrent_research_jobs` | `2` |
| `research_job_expiry_hours` | `24` |
| `spot_quote_currencies` | `USD`, `USDC`, `USDT` |
| `catalog_health` | `bounded_gap_inspection`, `ingest_self_complete`, `heartbeat_during_ingest`, `ranged_backfill`, `explicit_watch_ingest`, `research_lookback_ceilings` ([ADR 0085](../../decisions/0085-fast-research-ingest.md)) |
| `bounded_deployment_reads` | `list`, `summary`, `fills`, `orders` |
| `deployment_ledger_pagination` | `cursor` |
| `multi_book_ledger` | `paper`, `live` |
| `max_historical_interval_count` | `129600` |
| `backtest_engine` | `thytrader-backtest` — the single unified model ([ADR 0083](../../decisions/0083-unified-backtest-model.md)); replaced the `backtest_engines` list |
| `paper_timeframes` / `live_timeframes` | `1m` `5m` `15m` `30m` `1h` `2h` `4h` `6h` `1d` |
| `htf_filter_runtimes` | `research`, `paper`, `live` |
| `indicator_timeframe_runtimes` | `research`, `paper`, `live` |
| `indicator_offset_runtimes` | `research`, `paper`, `live` — per-declaration bar lag ([ADR 0086](../../decisions/0086-indicator-catalog-expansion-and-offset.md)) |
| `indicator_kinds` | the 53 implemented kinds in operator `indicators` order (`ema` … `constant`, then `dema` … `percent_rank`, ADR 0086) |
| `position_sides` | `long`, `short` |
| `attached_entry_brackets` | `paper`, `live` |
| `paper_deploy_fee_fields` | `maker_fee_rate`, `taker_fee_rate` |
| `experiential_model_engines` | `thytrader-experiential-train-v1` |
| `risk_breakers` | `daily_loss`, `drawdown` |
| `order_rate_limits` | `entry`, `cancel` |
| `reference_price_collars` | `paper`, `live` |
| `trade_reason_journals` | `paper`, `live` |
| `decision_journals` | `paper`, `live` — per-bar decision timeline, `bar_decisions` ([ADR 0087](../../decisions/0087-per-bar-decision-timeline.md)) |
| `multi_instrument_documents` | `research`, `paper`, `live` |
| `intra_strategy_pyramiding` | `research`, `paper`, `live` |
| `lifecycle_commands` | `none`, `stop_new_entries`, `flatten`, `managed_shutdown` |
| `deployment_capital_fields` | `allocated_capital`, `venue_available_quote`, `reserved_buying_power`, `inventory_cost`, `performance_equity`, `initial_equity`, `baseline_equity`, `high_water_mark_equity`, `utc_day_open_equity` |
| `breaker_latch_reset` | `paper`, `live` |

```mermaid
classDiagram
  class OpsContractPayload {
    id thytrader-ops-contract-v48
    max_historical_interval_count
    backtest_engine
    paper_timeframes
    live_timeframes
    htf_filter_runtimes
    indicator_timeframe_runtimes
    indicator_offset_runtimes
    indicator_kinds
    position_sides
    attached_entry_brackets
    paper_deploy_fee_fields
    experiential_model_engines
    risk_breakers
    order_rate_limits
    reference_price_collars
    trade_reason_journals
    decision_journals
    multi_instrument_documents
    intra_strategy_pyramiding
    lifecycle_commands
    deployment_capital_fields
    breaker_latch_reset
    strategy_model
    portfolio_model
    portfolio_modes
    portfolio_backtest_contract
    max_concurrent_portfolio_backtests
    async_backtest_job_statuses
    research_job_statuses
    max_concurrent_research_jobs
    research_job_expiry_hours
    spot_quote_currencies
    catalog_health
    bounded_deployment_reads
    deployment_ledger_pagination
    multi_book_ledger
    expected_schema_revision 0054
  }
  class HealthPayload {
    api_probed
    database_configured
    coinbase_credentials_configured
    ops_contract
    applied_schema_revision
  }
  class OperatorEnvelope {
    schema_version thytrader-operator-report-v1
    application_version
    generated_at UTC
    overall_status healthy|degraded|failed
    payload
  }
```
