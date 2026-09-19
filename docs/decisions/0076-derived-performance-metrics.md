# 0076: Keep Sharpe-class ratios as a derived backtest report

- Status: Accepted
- Date: 2026-09-19
- Relates to: [0011](0011-derived-buy-and-hold-benchmark.md)

## Context

Canonical `BacktestSummary` stores ledger facts only. Operators asked for Sharpe, Sortino,
Calmar, SQN, CAGR, and related ratios. Adding those fields to the identity-bearing result
would change newly produced fingerprints while old rows omit them.

## Decision

Expose `thytrader-performance-metrics-v1` as a derived report computed from the reverified
equity curve and closed trades:

- `GET /api/v1/backtests/{result_fingerprint}/metrics`
- sibling `metrics` on summary/full backtest detail
- `thytrader-research show-result` and `thytrader-operator performance`

Risk-free rate is 0. Annualization uses the median equity-curve bar clock
(`bars_per_year = 365 * 24 * 3600 / bar_seconds`). Buy-and-hold on this report is
mark-to-mark from first/last equity marks (no fees); fee-aware comparison remains
`thytrader-buy-and-hold-v1`. Canonical v1–v3 result bytes do not change.

## Consequences

Ratio metrics can evolve by bumping the metrics contract without rewriting stored
results. Study documents keep `BacktestSummary` only; operators open a child result
for Sharpe. Undefined ratios (insufficient samples, zero downside, zero drawdown)
are omitted rather than invented.

## Alternatives considered

Adding optional fields to `BacktestSummary` was rejected because even additive
canonical fields split identity across old and new rows.
