# Deterministic Bar-Level Backtest Simulation

## Purpose and boundary

`thytrader-bar-backtest-v1` turns one exact published V1 research run into an immutable simulated trade ledger, equity curve, drawdown series, and performance summary. `thytrader-bar-backtest-v2` keeps the same deterministic long-only bar event ordering while adding one disclosed constant bid-ask spread assumption to every modeled execution. Both are research-only components: neither can create an order intent, submit an order, connect to an exchange, or grant paper/live trading authority.

`thytrader-bar-v1` remains request-only and `thytrader-bar-signal-v1` remains signal-trace-only. They fail closed at this simulator boundary: a backtest requires a separately published run carrying the backtest engine contract, so old immutable request bytes never acquire new fill/PnL meaning.

The public command is:

```bash
uv run thytrader-research-run publish-backtest --strategy-fingerprint sha256:... \
  --dataset-fingerprint sha256:... --evaluation-start 2026-01-01T00:00:00Z \
  --evaluation-end 2026-03-01T00:00:00Z --initial-quote-balance 10000 \
  --maker-fee-rate 0.001 --taker-fee-rate 0.002 --fixed-slippage-bps 1 \
  --engine-contract-version thytrader-bar-backtest-v2 --spread-bps 10
uv run thytrader-backtest simulate <run_fingerprint> --pretty
uv run thytrader-backtest list --run-fingerprint <run_fingerprint>
uv run thytrader-backtest show <result_fingerprint> --pretty
```

The publication command derives warmup from the verified strategy and binds every execution-relevant assumption into a new backtest-engine run. It calculates a separate semantic execution fingerprint, so repeating the exact command reuses the previously verified immutable run rather than minting another equivalent request. Simulation loads and reverifies that run, its published strategy, immutable dataset manifest, and Parquet candles. It appends one canonical result to PostgreSQL; list and show are read-only and reverify output before returning it. Failures are generic and do not expose database URLs or artifacts.

## Source and result identity

The result stores these immutable source identities:

- published research-run fingerprint;
- published strategy fingerprint;
- immutable dataset fingerprint; and
- `thytrader-bar-backtest-v1` or `thytrader-bar-backtest-v2` engine-contract version; and
- for V2, a fully resolved broker-assumptions block containing the constant spread, full-fill policy,
  bid-side trigger policy, and bid-close equity-marking policy.

Canonical result JSON is sorted, compact UTF-8 JSON. Its SHA-256 fingerprint is the result identity. The authoritative service independently re-evaluates the signal trace and result persistence rejects a trace whose identity or source identities do not match the result. Publication revalidates unchecked in-memory models, source-row identities, canonical bytes, and the stored fingerprint on every load. Results are append-only and idempotent by result fingerprint; multiple engine versions may derive different results from the same run without rewriting earlier evidence.

## Decimal contract

The simulator runs every calculation inside `decimal64-half-even-v1`: precision 64, `ROUND_HALF_EVEN`, `Emin=-6143`, `Emax=6144`, and traps for invalid operations, division by zero, and overflow. Ambient process Decimal settings cannot change output. Result decimals are canonical plain strings and may use the context subnormal range down to exponent `-6206`.

There is no binary floating point conversion. Exchange increment quantization remains a future broker-boundary concern; this first research simulator deliberately does not claim venue-valid order quantities.

## Bar event ordering

For each evaluation candle the simulator uses this fixed sequence:

1. A `matched` entry condition becomes a pending entry only after that candle closes.
2. A pending entry fills exactly once at the **next candle open**, with adverse fixed slippage and the published **taker** fee assumption.
3. A time exit that has reached its completed-bar limit fills at that candle open before intrabar prices are considered.
4. Otherwise, the completed candle's low/high may trigger protective exits. If both initial stop and take-profit are reachable in the same OHLC candle, the simulator chooses the stop first.
5. The next completed-candle signal may schedule another entry only while flat.

The final next-open candle required by the published run may fill a final pending entry. Any position still open at that boundary is forcibly closed at that same open, with the normal adverse sell slippage and taker fee, reason `evaluation_end`. This makes the result ledger closed and reproducible without reading a candle outside the published input range.

The required final next-open candle is never passed into indicator or condition evaluation. No future high, low, close, or volume can influence prior signals or fills.

Publication eligibility applies the same boundary contract before simulation: a run whose final evaluation end cannot represent one additional hourly next-open candle is rejected with a controlled research-publication error rather than leaking a raw datetime overflow.

## Position and cost model

V1 is one BTC-USD-like long-only position at a time. It uses the declared ATR stop distance and risk fraction, bounded by the strategy maximum quote notional, portfolio exposure fraction, available quote cash including entry fees, and minimum quote notional. It does not create a trade if the calculated stop is non-positive or the minimum notional is unavailable.

Fills are modeled marketable at the next open. V1 buy fills multiply the raw open by `1 + slippage_bps / 10,000`; V1 sell fills multiply the applicable stop, target, time-exit open, or final open by `1 - slippage_bps / 10,000`. Both legs use `taker_fee_rate`. The currently declared maker fee and maker preference are intentionally not treated as evidence of a limit-order fill in this bar-level contract.

### V2 constant-spread stress model

V2 accepts exactly one explicit `spread_bps` assumption (zero through 1,000 total basis points). It is a **stress parameter**, not reconstructed historical order-book evidence: the system must not present it as an observed Coinbase bid/ask spread or as a prediction of live fills. Operators should compare the same strategy across disclosed values such as `0`, `10`, `25`, and `50` basis points and reject strategies that cease to work at plausible friction.

For a raw reference price `p` and total spread fraction `s = spread_bps / 10,000`, V2 uses `ask = p * (1 + s / 2)` for buys and `bid = p * (1 - s / 2)` for sells. It applies the existing adverse fixed slippage after the selected executable side. Long stop/target triggers compare the candle extreme at bid side before slippage. Open positions mark at bid close, so drawdown reflects pre-slippage liquidation value rather than an optimistic raw close.

Every V2 entry and exit records its raw reference price, executable side, and per-unit spread cost. The result summary records total spread cost. A V2 run with `spread_bps=0` must reproduce V1 trade economics exactly, although its run/result fingerprints remain distinct because the contract and disclosed broker evidence differ. Existing V1 canonical documents omit V2-only fields and remain byte-identical, loadable, and reverified.

The first schema profile explicitly disables trailing stops. No trailing-stop behavior is implied by this engine.

## Result fields

Every closed trade has exact entry/exit fills, notional, fee, fee rate, exit reason, gross PnL, net PnL, and holding bars. The equity curve contains cash, base quantity, mark price, and equity at every evaluation boundary plus the final required next-open boundary.

The summary includes initial/final equity, total PnL and return fraction, trade/win counts, gross profit/loss, win rate, profit factor when losses exist, average win/loss when defined, absolute and fractional maximum drawdown, and exposure/evaluation bars. It intentionally does not invent annualization, Sharpe-like statistics, or claims of live fill quality; buy-and-hold comparison is a separate derived report with its own versioned contract.

## Persistence

Migration `0007_published_backtest_results.py` creates `published_backtest_results`. Each row has a result fingerprint primary key, source identity columns, canonical result JSON, timestamp, source-run foreign key, and fingerprint format constraints. The source dataset has an index for result lookup; all result content remains inside the canonical document to preserve one audited identity. The PostgreSQL result store requires an application-managed research-run verifier and `DatasetStore`; there is no row-only source fallback, so a result store cannot become a benchmark source without full strategy, run, manifest, and coverage verification.

## Results API and bounded research submission

Three read-only endpoints expose stored evidence to the browser; `POST /api/v1/backtests` submits a
bounded deterministic historical simulation. No endpoint can mutate an immutable result or grant paper/live
trading authority.

- `GET /api/v1/backtests` returns a bounded newest-first page of summaries. Each row carries the result/run/strategy/dataset fingerprints, the engine-contract version, the publication timestamp, and the immutable `summary` metrics block. Summary metrics are projected from the canonical document server-side, so a list query never materializes a full trade ledger or equity curve. It accepts at most one source-fingerprint filter (`run_fingerprint`, `strategy_fingerprint`, or `dataset_fingerprint`), `limit` (1–100, default 50), and `offset` (≥ 0).
- `GET /api/v1/backtests/{result_fingerprint}` returns one complete result (full trade ledger, equity curve, and summary). It reuses the same fail-closed `load` path as the CLI `show` command: the stored canonical bytes, the result fingerprint, the row identity columns, and the linked source run publication are all reverified before anything is returned. A result is never served from stored JSON without reverification.
- `GET /api/v1/backtests/{result_fingerprint}/benchmark` returns a versioned `thytrader-buy-and-hold-v1` comparison derived from the same reverified result, source run, and immutable dataset. It buys at the first evaluation candle open, marks at completed evaluation closes, and liquidates at the published final next-open boundary using the source run's taker fee, fixed slippage, and V1/V2 fill assumptions. The response includes source identities, entry/exit evidence, modeled costs, return, maximum drawdown, and a canonical `benchmark_fingerprint` covering every other derived field; the API revalidates that identity before serialization. It is not part of canonical result bytes.

All three endpoints return redacted failure envelopes. A malformed fingerprint yields `400 backtest_invalid`; a well-formed but unknown result fingerprint yields `404 backtest_not_found`; storage or integrity failures yield `503 backtests_unavailable` with no internal detail. When durable result storage is not configured (no database URL), the routes fail closed with `503` rather than presenting empty results. Decimal values remain canonical strings at the API boundary; the browser formats them for display only, using exact string/`BigInt` arithmetic for monetary and percentage presentation rather than binary `Number` conversion.

## Explicitly not in this slice

- limit/maker order-book matching, latency distributions, rejections, or partial fills;
- observed bid/ask data ingestion or calibration of the V2 stress parameter to venue microstructure;
- shorts, margin, leverage, multiple positions, or cross-strategy portfolio allocation;
- trailing stops or walk-forward optimization;
- submitting a backtest from the API or dashboard, or any strategy authoring/mutation;
- paper broker, exchange adapters, Coinbase submission, or live execution.
