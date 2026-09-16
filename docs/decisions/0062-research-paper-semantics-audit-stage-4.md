# 0062: Research and paper semantics (audit stage 4)

- Status: Accepted
- Date: 2026-09-16
- Relates to: [0017](0017-maker-limit-bar-backtest.md), [0011](0011-derived-buy-and-hold-benchmark.md),
  [0035](0035-phase-11-research-rigor.md), [0044](0044-parameter-sweeps-wfo-stitched-equity.md),
  [0045](0045-spot-shorting-and-attached-entry-brackets.md), [0048](0048-paper-deploy-fee-fields.md)

## Context

An external audit (2026-09-16) found research-boundary leaks, optimistic fill assumptions,
clock mismatches between paper/live and backtest, and mislabeled study aggregates. Several issues
changed numerical semantics; others required explicit validity documentation without altering
immutable v1–v3 fingerprints.

## Decision

### Backtest engine `thytrader-bar-backtest-v4`

Introduce a fourth immutable engine contract. v1–v3 canonical bytes stay loadable and unchanged.

v4 matches v3 maker broker/bar_execution assumptions and adds:

1. **Causal evaluation terminal (F17):** loop only declared evaluation bars for intrabar
   matching; liquidate open positions at `evaluation.ends_at` **open** with no post-boundary
   intrabar TP/stop/entry processing.
2. **Taker slippage on emergency exits (F31):** honor published `fixed_slippage_bps` on stop,
   time, and evaluation-end liquidations; maker resting TP remains zero slippage.
3. **Causal trailing on the same bar (F18):** evaluate stops against the entering stop before
   ratcheting ATR trails for that bar.
4. **Validity limits (F23):** attach `validity_limits` on summaries naming maker touch-full-fill,
   TP-before-stop same-bar ordering, and spot-short synthetic inventory when applicable.

Prefer v4 for walk-forward selection, OOS claims, and paper/live comparison. Keep v3 when
reproducing pre-0062 evidence.

### Runtime and paper (no new engine contract)

- **Trailing (F18):** paper checks the working stop before ratcheting; live venue brackets
  unchanged.
- **Indicator history (F19):** worker fetches from deploy-anchored `warmup_starts_at` through the
  closed bar under evaluation (including catch-up `as_of`), not a sliding `warmup_bars` slice
  anchored only to now.
- **Paper fill time (F33):** immediate synthetic fills use `candle.starts_at` event time.
- **Entry bar bucket (F34):** `entered_bar` aligns to strategy interval via `entry_bar_bucket`,
  not minute rounding.
- **Live sizing fees (F24):** size with Coinbase maker tier when available; otherwise documented
  conservative reserve. Quantize executable prices before risk geometry.

### Research aggregates and benchmarks

- **Stitched OOS drawdown (F20):** track maximum contemporaneous percentage drawdown along the
  compounded path.
- **Buy-and-hold benchmark (F32):** derive bar count and contiguity from the run interval, not
  hourly assumptions.
- **Study OOS fields (F37):** when no OOS windows exist, OOS-named metrics are absent/zero; IS
  metrics use explicitly named fields.

### Dataset identity (F41)

Bump dataset manifest `schema_version` to **2** for new fingerprints: hash uses
`canonical_decimal` OHLCV while Parquet retains source spelling. v1 manifests verify unchanged.

### Indicator conventions

Document in `docs/architecture/indicators.md`: warmup origin, first-valid index per kind, and
empty-input output length (e.g. RSI returns zero rows for zero inputs).

## Consequences

- New research runs that need corrected boundaries and costs must name v4 explicitly.
- Operator and agent skills document v4, validity limits, truthful OOS fields, and indicator
  warmup parity expectations.
- Published v1–v3 results and v1 dataset fingerprints remain valid under their original contracts.

## Alternatives considered

- **Silently fix v3 in place:** rejected; ADR 0009/0017 forbid reinterpretation.
- **Persist indicator checkpoints in PostgreSQL:** deferred; deploy-anchored fetch restores parity
  without a migration in this slice.
