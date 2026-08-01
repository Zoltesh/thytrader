# Deterministic Bar-Level Backtest Simulation

## Purpose and boundary

`thytrader-bar-backtest-v1` turns one exact published `thytrader-bar-backtest-v1` research run into an immutable simulated trade ledger, equity curve, drawdown series, and performance summary. It is a research-only component: it cannot create an order intent, submit an order, connect to an exchange, or grant paper/live trading authority.

`thytrader-bar-v1` remains request-only and `thytrader-bar-signal-v1` remains signal-trace-only. They fail closed at this simulator boundary: a backtest requires a separately published run carrying the backtest engine contract, so old immutable request bytes never acquire new fill/PnL meaning.

The public command is:

```bash
uv run thytrader-backtest <run_fingerprint> --pretty
```

It loads and reverifies the published research run, its published strategy, immutable dataset manifest, and Parquet candles. It appends one canonical result to PostgreSQL and writes the canonical result JSON to standard output; its result fingerprint is written to standard error. Failures are generic and do not expose database URLs or artifacts.

## Source and result identity

The result stores these immutable source identities:

- published research-run fingerprint;
- published strategy fingerprint;
- immutable dataset fingerprint; and
- `thytrader-bar-backtest-v1` engine-contract version.

Canonical result JSON is sorted, compact UTF-8 JSON. Its SHA-256 fingerprint is the result identity. Publication revalidates unchecked in-memory models, source-row identities, canonical bytes, and the stored fingerprint on every load. Results are append-only and idempotent by result fingerprint; multiple engine versions may derive different results from the same run without rewriting earlier evidence.

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

## Position and cost model

V1 is one BTC-USD-like long-only position at a time. It uses the declared ATR stop distance and risk fraction, bounded by the strategy maximum quote notional, portfolio exposure fraction, available quote cash including entry fees, and minimum quote notional. It does not create a trade if the calculated stop is non-positive or the minimum notional is unavailable.

Fills are modeled marketable at the next open. Buy fills multiply the open by `1 + slippage_bps / 10,000`; sell fills multiply the applicable stop, target, time-exit open, or final open by `1 - slippage_bps / 10,000`. Both legs use `taker_fee_rate`. The currently declared maker fee and maker preference are intentionally not treated as evidence of a limit-order fill in this bar-level contract.

The first schema profile explicitly disables trailing stops. No trailing-stop behavior is implied by this engine.

## Result fields

Every closed trade has exact entry/exit fills, notional, fee, fee rate, exit reason, gross PnL, net PnL, and holding bars. The equity curve contains cash, base quantity, mark price, and equity at every evaluation boundary plus the final required next-open boundary.

The summary includes initial/final equity, total PnL and return fraction, trade/win counts, gross profit/loss, win rate, profit factor when losses exist, average win/loss when defined, absolute and fractional maximum drawdown, and exposure/evaluation bars. It intentionally does not invent annualization, Sharpe-like statistics, benchmark returns, or claims of live fill quality.

## Persistence

Migration `0007_published_backtest_results.py` creates `published_backtest_results`. Each row has a result fingerprint primary key, source identity columns, canonical result JSON, timestamp, source-run foreign key, and fingerprint format constraints. The source dataset has an index for result lookup; all result content remains inside the canonical document to preserve one audited identity.

## Explicitly not in this slice

- limit/maker order-book matching, latency distributions, spread models, rejections, or partial fills;
- shorts, margin, leverage, multiple positions, or cross-strategy portfolio allocation;
- trailing stops, walk-forward optimization, benchmark comparison, results API, or dashboard;
- paper broker, exchange adapters, Coinbase submission, or live execution.
