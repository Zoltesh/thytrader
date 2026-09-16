# Strategy schema

Canonical published document. Field rules:
[canonical strategy schema](../canonical-strategy-schema.md).
Model: `thytrader.strategies.models.StrategyDefinition`.

Fingerprint is `sha256:` of sorted compact UTF-8 JSON over the entire published
document. Unknown fields are rejected. One product per document.
`max_concurrent_positions` and `max_open_positions` are `1`. `entry.side` is
`long` or `short`. Venue clocks: `1m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`,
`6h`, `1d`.

```mermaid
classDiagram
  class StrategyDefinition {
    schema_version 1.0
    strategy_id UUIDv7
    version int
    name string
    description string?
    status draft|published|archived
    created_at UTC
    timeframe venue clock
  }
  class Instrument {
    product_id BASE-USD
    base_currency
    quote_currency USD
  }
  class DataRequirements {
    warmup_bars
    required_fields OHLCV
  }
  class IndicatorDefinition {
    id
    kind
    input
    parameters
    timeframe? coarser clock
  }
  class HigherTimeframeFilter {
    timeframe coarser integer multiple
    when ConditionGroup
  }
  class EntryDefinition {
    side long|short
    when ConditionGroup
    cooldown_bars
    max_open_positions 1
  }
  class RiskFractionSizing {
    kind risk_fraction
    risk_fraction
    min_quote_notional
    max_quote_notional
  }
  class PortfolioLimits {
    max_strategy_exposure_fraction
    max_concurrent_positions 1
  }
  class ExitDefinition {
    initial_stop atr_multiple
    take_profit reward_risk
    trailing_stop
    time_exit
  }
  class ExecutionPreferences {
    entry_preference maker_only|marketable_limit
    max_entry_wait_bars
    on_unfilled_entry cancel|reprice
  }
  StrategyDefinition --> Instrument
  StrategyDefinition --> DataRequirements
  StrategyDefinition --> IndicatorDefinition : 1..20
  StrategyDefinition --> HigherTimeframeFilter : optional
  StrategyDefinition --> EntryDefinition
  StrategyDefinition --> RiskFractionSizing
  StrategyDefinition --> PortfolioLimits
  StrategyDefinition --> ExitDefinition
  StrategyDefinition --> ExecutionPreferences
  HigherTimeframeFilter --> IndicatorDefinition : HTF ids disjoint
  HigherTimeframeFilter --> DataRequirements
```

```mermaid
flowchart TD
  Draft["draft editable"] -->|publish| Published["published immutable"]
  Published -->|revise POST .../revise| NextDraft["next version draft"]
  NextDraft -->|publish| NextPublished["published vN+1"]
  Published -->|archive marker| Archived["archived hidden from active selection"]
  Archived -.->|historical refs remain valid| Published
```

`htf_filter` is omitted from canonical JSON when null. Optional per-indicator
`timeframe` is omitted when absent. Paper and live evaluate `htf_filter` on
last-completed complete-only HTF bars. Multi-instrument documents are not legal
here.
