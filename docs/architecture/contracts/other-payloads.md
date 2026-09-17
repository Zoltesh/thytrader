# Other durable payloads

Additional shipped contracts operators and agents already share. Not destination
remainders.

## Dataset manifest

Worker-owned complete-only Parquet. Model: `thytrader.market_data.datasets.DatasetManifest`.
`complete` is island completeness. Judge a watch by `watch_complete`. Missing
candles are never interpolated.

```mermaid
classDiagram
  class DatasetManifest {
    provider
    product_id
    timeframe venue clock
    starts_at ends_at half-open
    expected_candle_count
    received_candle_count
    gap_count missing_intervals
    complete island
    content_fingerprint sha256
    files
    manifest_path
  }
```

## Risk-policy registry

Immutable `thytrader-risk-policy-v1`. See [execution](execution.md) for the
entry gate. Fingerprint is `sha256:` of canonical policy JSON.

```mermaid
classDiagram
  class RiskPolicyDefinition {
    schema_version thytrader-risk-policy-v1
    policy_id UUID
    version int
    quote_currency USD|USDC
    product_allowlist 0..32 BASE-USD|BASE-USDC
    max_concurrent_running_deployments 1..32
    max_concurrent_open_positions 1..32
    max_portfolio_exposure_fraction
    per_product_max_exposure_fraction
    paper_capital_quote
    allocations
    daily_loss_limit_fraction
    max_strategy_drawdown_fraction
    max_entry_orders_per_minute
    max_cancellations_per_minute
    reference_price_collar_fraction
    allow_intra_strategy_pyramiding omitted when false
  }
  class CapitalAllocation {
    strategy_id
    allocated_quote
  }
  class ActiveRiskPolicy {
    policy_fingerprint sha256
    source compiled_default|published
  }
  RiskPolicyDefinition --> CapitalAllocation : unique strategy_id
  ActiveRiskPolicy --> RiskPolicyDefinition
```

Daily-loss / drawdown breakers, order-rate limits, and reference-price collars
are on this document ([ADR 0050](../../decisions/0050-daily-loss-drawdown-rate-collars.md)).
`allow_intra_strategy_pyramiding` is omitted from canonical JSON when false so
compiled-default fingerprints stay stable
([ADR 0056](../../decisions/0056-multi-instrument-documents-and-pyramiding.md)).

## Operator report envelope

Every machine-readable `thytrader-operator` report. Schema:
`thytrader-operator-report-v1`.

```mermaid
classDiagram
  class OperatorEnvelope {
    schema_version thytrader-operator-report-v1
    application_version
    generated_at UTC
    timezone UTC
    overall_status healthy|degraded|failed
    recommended_next_action
  }
  class ComponentReport {
    name
    status
    reason_code
    detail
  }
  class RedactionMetadata {
    secrets_redacted
    raw_environment_omitted
    account_identifiers_omitted
    balances_omitted
  }
  OperatorEnvelope --> ComponentReport
  OperatorEnvelope --> RedactionMetadata
```

Report kinds: health, configuration, exchange, market_data, data_catalog,
products, indicators, strategies, performance, risk, reconciliation, runtime,
monitor, studies, trade_reasons, support_bundle.

## Experiential memory hooks

`thytrader-experiential-memory-v1` journals. Origin `human` or `agent` is
required. Bounded V1 training is `thytrader-experiential-train-v1` (advisory
only; [ADR 0049](../../decisions/0049-experiential-train-v1.md)). Per-trade
“why it was made” records are `thytrader-trade-reason-v1`
([ADR 0054](../../decisions/0054-trade-reason-journals.md)). The trainer
consumes `JournalEntry` as stored.

```mermaid
classDiagram
  class JournalEntry {
    schema_version thytrader-experiential-memory-v1
    origin human|agent
    kind fact|lesson|note
    title body
    evidence_kind
    evidence_id?
    product_id?
    runtime_mode none|research|paper|live
    lesson_outcome
  }
  class ExperientialModel {
    schema_version thytrader-experiential-model-v1
    engine_id thytrader-experiential-train-v1
    seed
    fingerprint
    advisory
  }
  class TradeReasonRecord {
    schema_version thytrader-trade-reason-v1
    origin human|agent|runtime
    intent_id
    strategy?
    signal
    risk
    notes
    reconcile
  }
  ExperientialModel ..> JournalEntry : trains from evidenced rows
  TradeReasonRecord ..> JournalEntry : distinct why-trade schema
```
