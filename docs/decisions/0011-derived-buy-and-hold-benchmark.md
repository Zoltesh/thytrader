# 0011: Keep buy-and-hold comparison as a derived backtest report

- Status: Accepted
- Date: 2026-08-03

## Context

Backtest results are immutable canonical evidence. Their fingerprints, persisted bytes, and V1/V2 semantics must not change when the dashboard gains additional comparative analytics. A buy-and-hold comparison is useful for interpreting an existing result, but it is not part of the strategy simulation ledger and must not be reconstructed from incomplete or unverified inputs.

## Decision

Expose buy-and-hold as a versioned, read-only derived report at:

```text
GET /api/v1/backtests/{result_fingerprint}/benchmark
```

The reader re-verifies the result, its published research-run specification, and its immutable dataset before calculating `thytrader-buy-and-hold-v1`. The benchmark buys at the first evaluation candle open, marks the held asset at each completed evaluation close, and liquidates at the published final next-open boundary. It uses the source run's taker fee, fixed slippage, and V1/V2 fill model, including V2 constant-spread ask/bid pricing and bid-close marking.

The benchmark response carries the source identities, execution assumptions, entry/exit evidence, costs, net return, and maximum drawdown. It is not persisted as part of canonical result JSON and cannot mutate or re-fingerprint an existing result. If the result, source run, or dataset cannot be reverified, the endpoint fails closed with a redacted `503` response.

The PostgreSQL result repository therefore requires the full research-run verifier and immutable dataset store at construction time. A row-only source lookup is not an acceptable benchmark boundary, even when the result row itself contains matching strategy and dataset fingerprints.

## Consequences

- Existing V1 and V2 result bytes and fingerprints remain unchanged.
- The comparison is reproducible only while the exact published result and dataset remain available and verifiable.
- The first benchmark contract is a transparent baseline, not annualized performance, a quote-history reconstruction, or a live-fill prediction.
- A later benchmark contract may add a different benchmark or richer report without changing this contract; it must carry its own version and assumptions.

## Alternatives considered

- **Add benchmark fields to `BacktestResult`:** rejected because even additive canonical fields would change existing result bytes and identities.
- **Derive the benchmark only from the stored equity curve:** rejected because it would lose the source fee, spread, and entry-boundary evidence and could silently compare different assumptions.
- **Persist a mutable benchmark table:** deferred because the report is deterministic from already verified immutable inputs; persistence would add synchronization and invalidation surface without improving provenance in this slice.
