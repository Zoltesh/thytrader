# Backtest result

Immutable simulation evidence for one research-run. Not order authority.
Field rules: [backtest simulation](../backtest-simulation.md).
Model: `thytrader.backtest.models.BacktestResult`.

Canonical result JSON is sorted compact UTF-8. Its SHA-256 fingerprint is the
result identity. Append-only PostgreSQL `published_backtest_results`. Load
reverifies bytes, fingerprint, row identity, and the source run. Buy-and-hold
comparison is a **derived** `thytrader-buy-and-hold-v1` report. Sharpe-class
ratios are a **derived** `thytrader-performance-metrics-v1` report. Neither is
part of canonical result bytes.

```mermaid
classDiagram
  class BacktestResult {
    schema_version 1.0
    engine_contract_version V1|V2|V3
    run_fingerprint
    strategy_fingerprint
    dataset_fingerprint
    signal_trace_fingerprint
  }
  class BacktestTrade {
    gross_pnl
    net_pnl
    holding_bars
  }
  class BacktestFill {
    candle_starts_at UTC
    price quantity notional
    fee fee_rate
    reference_price?
    executable_side?
    spread_cost?
  }
  class BacktestExitFill {
    reason stop_loss|take_profit|time_exit|evaluation_end
  }
  class EquityPoint {
    candle_starts_at
    cash
    base_quantity
    mark_price
    equity
  }
  class BacktestSummary {
    initial_equity final_equity
    total_net_pnl total_return_fraction
    trade_count winning_trade_count
    maximum_drawdown
    exposure_bars evaluation_bars
    total_spread_cost? V2
  }
  class BrokerAssumptions {
    V2 or V3 only
  }
  BacktestResult --> BacktestTrade : trades
  BacktestResult --> EquityPoint : equity_curve min 1
  BacktestResult --> BacktestSummary
  BacktestResult --> BrokerAssumptions : omitted on V1
  BacktestTrade --> BacktestFill : entry
  BacktestTrade --> BacktestExitFill : exit
  BacktestExitFill --|> BacktestFill
```

```mermaid
flowchart TD
  Run["ResearchRunSpecification"] --> Sim["bar simulator decimal64-half-even-v1"]
  Strat["Published strategy"] --> Sim
  Data["Verified complete-only candles"] --> Sim
  Sim --> Result["BacktestResult fingerprint"]
  Result --> API["GET /api/v1/backtests/..."]
  Result --> BH["derived buy-and-hold-v1\nnot in result bytes"]
```

V1 omits `broker`. V2/V3 results must carry the same broker block as the source
run. Existing long V1/V2/V3 golden fingerprints stay byte-identical when shorts
are unused. The simulator does not create an order intent.
