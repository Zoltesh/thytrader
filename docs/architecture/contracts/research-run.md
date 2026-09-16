# Research-run specification

Immutable research **request**, not proof a backtest ran and not order authority.
Field rules: [research-run specification](../research-run-specification.md).
Model: `thytrader.research.models.ResearchRunSpecification`.

PostgreSQL table `published_research_run_specs`. Publication reverifies the
published strategy, verified dataset, and existing strategy/dataset binding.
Fingerprint is `sha256:` of the canonical document.

```mermaid
classDiagram
  class ResearchRunSpecification {
    schema_version 1.0
    run_id UUIDv7 matches created_at ms
    created_at UTC
    strategy_fingerprint sha256
    dataset_fingerprint sha256 LTF
    htf_dataset_fingerprint sha256?
    engine_contract_version
    random_seed
  }
  class EvaluationWindow {
    starts_at UTC candle boundary
    ends_at half-open
  }
  class WarmupWindow {
    bars
    starts_at derived
  }
  class CapitalAssumptions {
    quote_currency USD
    initial_quote_balance
  }
  class CostAssumptions {
    maker_fee_rate
    taker_fee_rate
    fixed_slippage_bps
  }
  class BrokerAssumptions {
    price_model
    spread_bps
    fill_policy
    trigger_evaluation
    equity_marking
  }
  class BarExecutionAssumptions {
    signal_timing completed_candle_close
    fill_timing next_candle_open or resting_maker_limit
  }
  class IndicatorTimeframeDataset {
    timeframe
    dataset_fingerprint
  }
  ResearchRunSpecification --> EvaluationWindow
  ResearchRunSpecification --> WarmupWindow
  ResearchRunSpecification --> CapitalAssumptions
  ResearchRunSpecification --> CostAssumptions
  ResearchRunSpecification --> BrokerAssumptions : required for V2/V3
  ResearchRunSpecification --> BarExecutionAssumptions
  ResearchRunSpecification --> IndicatorTimeframeDataset : unbound extra TFs
```

```mermaid
flowchart TD
  Engines["engine_contract_version"]
  Engines --> V1req["thytrader-bar-v1 request-only"]
  Engines --> Signal["thytrader-bar-signal-v1 traces"]
  Engines --> B1["thytrader-bar-backtest-v1 next-open taker"]
  Engines --> B2["thytrader-bar-backtest-v2 constant spread"]
  Engines --> B3["thytrader-bar-backtest-v3 resting maker limit"]
  B2 --> BrokerV2["broker required\nconstant_spread_bps"]
  B3 --> BrokerV3["broker required\npost_only_limit"]
```

HTF dataset fingerprint is required iff the strategy declares `htf_filter`, must
differ from the LTF dataset, and is omitted from canonical JSON when null.
`indicator_dataset_fingerprints` bind unbound extra indicator clocks. Warmup
must equal `evaluation.starts_at` minus `warmup.bars` times the LTF duration.
The LTF dataset must cover one extra decision bar after evaluation end for
next-open fill data.
