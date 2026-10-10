# Deterministic Bar-Level Backtest Simulation

## Purpose and boundary

ThyTrader has **one** backtest model, identified as `engine: "thytrader-backtest"`
([ADR 0083](../decisions/0083-unified-backtest-model.md)). It turns one exact published research
run into an immutable simulated trade ledger, equity curve, drawdown series, and performance
summary using the same resting maker-limit loop that paper and live trade (its semantics are the
corrected maker model of [ADR 0062](../decisions/0062-research-paper-semantics-audit-stage-4.md)).
There is no engine selector and no versioned engine family: every backtest, study window, sweep
candidate, and benchmark uses this model.

It is a research-only component: it cannot create an order intent, submit an order, connect to an
exchange, or grant paper/live trading authority. Results are **simulated research evidence, not a
promise** of paper or live performance.

The public commands are:

```bash
uv run thytrader-research backtest-model            # describe the model's assumptions
uv run thytrader-research submit-backtest --file start.json --confirm
uv run thytrader-research-run publish-backtest --strategy-fingerprint sha256:... \
  --dataset-fingerprint sha256:... --evaluation-start 2026-01-01T00:00:00Z \
  --evaluation-end 2026-03-01T00:00:00Z --initial-quote-balance 10000 \
  --maker-fee-rate 0.001 --taker-fee-rate 0.002 --fixed-slippage-bps 1 --spread-bps 10
uv run thytrader-backtest simulate <run_fingerprint> --pretty
uv run thytrader-backtest list --run-fingerprint <run_fingerprint>
uv run thytrader-backtest show <result_fingerprint> --pretty
```

The supported operator path is `thytrader-research submit-backtest` (or `POST /api/v1/backtests`)
with a `strategy_id`, which snapshots the strategy automatically
([ADR 0082](../decisions/0082-strategy-root-mutable-strategies-auto-snapshots.md)). The
`thytrader-research-run` / `thytrader-backtest` developer commands take an existing strategy
**snapshot** fingerprint. `--spread-bps` is optional (default `0`); the retired
`--engine-contract-version` flag exits with a removal message.

Publication derives warmup from the verified strategy snapshot and binds every execution-relevant
assumption into one run. A separate semantic execution fingerprint makes repeating the exact
request reuse the verified immutable run. Simulation reloads and reverifies the run, strategy
snapshot, immutable dataset manifest, and Parquet candles, then appends one canonical result to
PostgreSQL; list and show reverify output before returning it. Failures are generic and do not
expose database URLs or artifacts.

## Source and result identity

A run specification binds: strategy snapshot fingerprint, dataset fingerprint(s) (HTF,
per-indicator timeframes, additional instruments), the evaluation and warmup windows, capital,
costs (`maker_fee_rate`, `taker_fee_rate`, `fixed_slippage_bps`, `spread_bps`), the random seed,
and `engine: "thytrader-backtest"`. The result stores the run, strategy snapshot, dataset, and
primary signal-trace fingerprints, the same `engine`, the trades, the equity curve, and the
summary.

Canonical result JSON is sorted, compact UTF-8 JSON; its SHA-256 fingerprint is the result
identity. `engine` stays inside canonical bytes so each fingerprint binds the simulator that
produced it; a future semantic change needs a superseding ADR and a new identity string. The
authoritative service independently re-evaluates the signal trace, and persistence rejects a
trace or source run whose identities or `engine` do not match the result. Results are
append-only and idempotent by result fingerprint.

A request that still sends `engine_contract_version` (HTTP body, study body, job payload, or CLI
file) is rejected with "engine_contract_version was removed: ThyTrader has one backtest model
(ADR 0083)…". Canonical run documents reject `engine_contract_version`, `broker`, and
`bar_execution` as unknown fields.

## Decimal contract

The simulator runs every calculation inside `decimal64-half-even-v1`: precision 64,
`ROUND_HALF_EVEN`, `Emin=-6143`, `Emax=6144`, and traps for invalid operations, division by zero,
and overflow. Ambient process Decimal settings cannot change output. Result decimals are canonical
plain strings and may use the context subnormal range down to exponent `-6206`.

There is no binary floating point conversion. Exchange increment quantization remains a broker
boundary concern; the simulator does not claim venue-valid order quantities.

## Bar event ordering

Signals are evaluated on completed candles only ([ADR 0008](../decisions/0008-deterministic-signal-evaluation.md)). For
each evaluation candle, and for each covered product in lexicographic `product_id` order, the
simulator runs this fixed sequence:

1. **Resting entry.** A limit rested on an earlier bar fills iff this bar trades through it
   (`low <= limit` for a long buy, `high >= limit` for a short sell), at the posted limit, with
   the **maker** fee, no slippage, and no spread. Otherwise the wait count increases; when it
   reaches `execution.max_entry_wait_bars`, `on_unfilled_entry=cancel` drops it and starts at
   least one bar of cooldown, and `reprice` re-rests it at this bar's close (same quantity,
   stop, and target).
2. **Stop (always first).** If the bar's executable extreme trades through the working stop, the
   position exits as a taker at `min(open, stop)` (`max` for shorts: a gap fills at the worse
   open), with the taker fee and `fixed_slippage_bps`. This is checked **before** the take-profit
   on every bar: a candle that touches both cannot say which traded first, so the conservative
   exit is assumed. Paper applies the same rule; live is decided by Coinbase. Results disclose
   it as `stop_before_tp_same_bar`. On the **fill bar** the stop is the only eligible exit: the
   take-profit is not resting yet.
3. **Resting take-profit.** From the bar after the fill bar, a take-profit rests at the target.
   If the stop did not trigger and this bar touches the target (`high >= target`, `low <= target`
   for shorts), it fills at the target with the maker fee.
4. **Trailing.** Enabled ATR trailing ratchets after the stop check, sharing the paper/live
   ratchet (`thytrader.trading.trailing`); the fill bar records the trail extreme without
   raising the stop. Disabled trailing is a no-op.
5. **Signal exit.** When the strategy declares `exits.signal_exit`
   ([ADR 0093](../decisions/0093-signal-based-exits.md)) and its rule matched at this bar's close,
   the position sells at that close as a taker (taker fee, slippage, half the spread stress). It is
   never evaluated on the fill bar, and it runs only after the stop and the resting take-profit
   had their chance on this bar, so a bar that touched either exits there instead. Results
   disclose `signal_exit_at_close`: live can only sell right after that close, which on a 24/7
   venue is the next bar's open.
6. **Time exit.** A position held `exits.time_exit.max_bars_held` completed bars sells at this
   bar's close as a taker (taker fee, slippage). When the signal exit is due on the same close it
   names the exit (same price). A bar that trades through the stop exits as a stop even when the
   time exit is also due. Paper resolves every same-bar tie in this order too (ops contract
   `same_bar_exit_precedence`; parity-tested per combination,
   [ADR 0097](../decisions/0097-runtime-parity-and-observability.md)).
7. **New entry.** A `matched` close-time signal rests a new post-only limit at this bar's close
   when the book is flat, off cooldown, and under `max_concurrent_positions`, or a same-side
   pyramiding add when the strategy allows it and the position is in profit. A signal never
   fills on its own bar.
8. **Mark.** Equity marks every open position at this bar's close.

After the last evaluation bar, the bar starting at `evaluation.ends_at` is used **only** to
liquidate open inventory at its **open** as a taker (reason `evaluation_end`, taker fee,
slippage). Any unfilled resting entry is dropped. No entry, take-profit, stop, or trailing logic
runs on that bar, and its high, low, close, and volume cannot affect the result. The required
bar is never passed to indicator or condition evaluation, so no future value influences a prior
signal or fill. Publication rejects a run whose evaluation end cannot represent that one extra
bar.

## Position, sizing, and cost model

Entries are sized at the limit price from the signal bar's ATR: stop distance
`atr * initial_stop.multiple`, stop at `limit - distance` (`+` for shorts), target at
`limit + distance * take_profit.multiple` (`-` for shorts), or no target when
`take_profit` is `{"kind": "none"}`. Risk quantity
`cash * risk_fraction / distance` is bounded by `max_quote_notional`, portfolio exposure
fraction, available quote cash including the maker fee (less a one-part-per-trillion headroom so a
cash-capped entry always funds at fill; [ADR 0083 amendment 2026-10-02](../decisions/0083-unified-backtest-model.md)),
and `min_quote_notional`. A
non-positive stop distance, a non-positive stop or target, or an unavailable minimum notional
skips the entry **with a named reason** (`stop_distance_not_positive`, `stop_not_positive`,
`target_not_positive`, `notional_below_minimum`, `insufficient_cash`) from the same
`execution/geometry.entry_levels` that paper and live use
([ADR 0090](../decisions/0090-research-correctness-optional-take-profit-diagnostics.md)). Shorts sell to open (credit quote cash) and buy to cover; there is no borrow,
margin, or funding model (`spot_short_synthetic`).

Pyramiding adds (when `entry.pyramiding` is enabled and `max_open_positions` allows) size against
the existing stop, rest as maker limits, and volume-weight into the open position without moving
its stop or target.

Multi-instrument documents ([ADR 0056](../decisions/0056-multi-instrument-documents-and-pyramiding.md))
run every covered product on each shared bar against one quote balance, in lexicographic
`product_id` order, capped by `portfolio_limits.max_concurrent_positions`. Stored
`signal_trace_fingerprint` remains the primary instrument's trace; extra products still fail
closed if their candles or traces cannot be verified.

| Leg | Fee | Slippage | Spread stress |
|---|---|---|---|
| Resting entry / pyramiding add | maker | none | none |
| Resting take-profit | maker | none | none |
| Stop exit | taker | `fixed_slippage_bps` | yes |
| Time exit (close) | taker | `fixed_slippage_bps` | yes |
| Signal exit (close) | taker | `fixed_slippage_bps` | yes |
| Evaluation-end liquidation (open) | taker | `fixed_slippage_bps` | yes |

Fee rates, slippage, and spread are modeled inputs (`CostAssumptions`), never observed Coinbase
fills.

### Optional spread stress

`costs.spread_bps` (zero through 1,000 total basis points, default `0`) is a disclosed constant
bid-ask spread **stress**, not reconstructed order-book evidence; do not present it as an
observed Coinbase spread or a prediction of live fills. Compare the same strategy at `0`, `10`,
`25`, and `50` bps and reject strategies that stop working at plausible friction.

For a raw reference price `p` and total fraction `s = spread_bps / 10,000`, the stressed ask is
`p * (1 + s/2)` and the stressed bid is `p * (1 - s/2)`:

- taker sells (long exits) fill at the bid and taker buys (short covers) at the ask, then pay
  fixed slippage from that side;
- long stops trigger when the stressed bid of the bar low reaches the stop (short stops: the
  stressed ask of the high), and a gapped stop fills at the worse of the stressed open and stop;
- open longs mark at the stressed bid close, open shorts at the stressed ask close;
- resting maker entries and take-profits still fill at their posted limit.

Spread-stressed taker fills record `reference_price`, `executable_side` (`ask`/`bid`), and
per-unit `spread_cost`; the summary records `total_spread_cost`. With `spread_bps = 0` none of
those fields appear and results are byte-identical to runs that omit the field.

## Diagnostics

Every simulation also returns a `thytrader-backtest-diagnostics-v1` entry funnel, stored in
`published_backtest_results.diagnostics_json` **beside** the canonical result (not in its bytes or
fingerprint): `signals_matched`, `entries_rested`, `entries_filled`, `entries_expired`,
`entries_repriced`, `entries_refused_at_fill` (shared cash no longer covered a fill),
`entries_unfilled_at_end`, `entries_size_capped` (a notional cap clamped the size; caps never skip),
`warmup_bars` (evaluation bars whose rule was undefined), `skipped[{reason, count}]` with the
gates `pending_entry`, `cooldown`, `max_positions`, `in_position`, `expiry_window` (futures) and
every geometry/sizing reason (futures add `below_one_contract`),
and `exit_reasons[{reason, count}]` (closed trades per exit reason, summing to the trade count;
`null` on diagnostics recorded before [ADR 0093](../decisions/0093-signal-based-exits.md)).
The model rejects an incoherent funnel: `signals_matched = entries_rested + Σ skipped` and
`entries_rested = filled + expired + refused_at_fill + unfilled_at_end`. Results published before
Alembic 0055 report `diagnostics: null` until an identical run is published again.

## Result fields

Every closed trade has exact entry/exit fills, notional, fee, fee rate, exit reason
(`stop_loss`, `take_profit`, `time_exit`, `signal`, `evaluation_end`, and for futures
`liquidation` and `expiry`), gross PnL, net PnL, and holding bars; perp futures trades add
`funding` (signed, included in net PnL) and their summary adds `total_funding`. The equity curve holds cash, base quantity (negative for shorts, `0` when several books
are open), mark price, and equity at every evaluation close plus one terminal point at
`evaluation.ends_at` after liquidation.

The summary includes initial/final equity, total PnL and return fraction, trade/win counts,
gross profit/loss, win rate, profit factor when losses exist, average win/loss when defined,
absolute and fractional maximum drawdown, exposure and evaluation bars, `total_spread_cost` when
spread stress is active, and `validity_limits` — always `maker_touch_full_fill` (candles do not
show queue position, so a touched limit is assumed to fill completely) and
`stop_before_tp_same_bar`, plus `spot_short_synthetic` for short strategies,
`signal_exit_at_close` for strategies that declare `exits.signal_exit`, and
`synthetic_no_trade_bars` when the evaluation window holds flat zero-volume bars for intervals
without trades, and the `futures_*` limits of a futures run (see below) ([ADR 0095](../decisions/0095-sparse-markets-no-trade-bars-listing-floors.md)). Those bars are evaluated like any other: the price
stays at the previous close, so no stop or target can trigger on them, and volume indicators read
them as undefined. It does not invent
annualization or Sharpe-like statistics inside canonical bytes; those live on the derived
`thytrader-performance-metrics-v1` report ([ADR 0077](../decisions/0077-derived-performance-metrics.md)),
and fee-aware buy-and-hold is a separate `thytrader-buy-and-hold-v1` report.

## Futures runs

[ADR 0128](../decisions/0128-futures-backtest-model.md). A futures strategy
(`instrument.kind: future`) needs a run spec with `instrument_contract` and `margin` (and
`funding` for perps); a mismatch either way fails `FUTURES_SPEC_MISMATCH`. The loop and the
cash-and-inventory ledger are the spot ones, because quantities are base units (contracts ×
`contract_size`); `src/thytrader/backtest/kernel_futures.py` holds the futures-only rules and spot
runs never reach them, so spot result bytes are unchanged.

- **Sizing.** ATR risk sizing requests a notional; the entry is the floor of whole contracts
  within four bounds that hold after the entry fee (rate × notional + `fee_per_contract` ×
  contracts): notional ≤ `max_leverage` × equity, maintenance ≤ (1 − buffer) × equity, initial
  margin ≤ `max_strategy_exposure_fraction` × equity, and notional ≤ `max_quote_notional`. Zero
  contracts skips with `below_one_contract`. Initial margin uses the stressed side rate;
  maintenance is `maintenance_fraction_of_initial` of it. The spot cash check does not apply, so a
  levered long's cash goes negative while equity stays cash plus marked inventory. Futures
  documents cannot pyramid.
- **Per bar.** (1) A dated contract at or past `expires_at − flatten_before_expiry_hours`
  cancels its resting entry and closes at the bar open as a taker (`expiry`); nothing else runs
  on that bar, and entries whose fill bar would start there skip with `expiry_window`. (2) The
  resting entry is matched. (3) Liquidation: if equity at the bar's adverse extreme (low for
  longs, high for shorts) is below maintenance, the position closes at that extreme as a taker
  (`liquidation`), before stops and targets. (4) Stops, targets, trails, signal and time exits as
  for spot. (5) Funding: every funding hour T with bar start < T ≤ bar end is charged at the bar
  close while the position is still open (cash −= signed quantity × close × rate).
- **Funding input.** A recorded series is passed to the kernel as `funding_rates` (funding hour →
  rate). It must hold exactly the funding hours in (`evaluation.starts_at`,
  `evaluation.ends_at`], match `funding.settled_hours`, and hash to `funding.series_fingerprint`
  (`funding_series_fingerprint`), or the run fails `FUNDING_HISTORY_MISSING` (naming the first
  missing hour) or `FUNDING_SERIES_MISMATCH`. A declared `constant_rate` takes no series.
- **Refusals.** A window ending after a dated contract's expiry fails
  `FUTURES_WINDOW_PAST_EXPIRY`; a dated strategy without `flatten_before_expiry_hours` fails
  `FUTURES_EXPIRY_UNSET`.
- **Limits.** Every futures result lists `futures_constant_margin`,
  `futures_conservative_liquidation` and `futures_shared_usdc_collateral`; a declared constant
  rate adds `futures_constant_funding`, and perps on bars longer than one hour add
  `futures_funding_at_bar_close`. Futures shorts are real and never list `spot_short_synthetic`.

## Persistence

`published_research_run_specs` and `published_backtest_results` hold canonical run and result
JSON with fingerprint primary keys, source identity columns, `strategy_id` foreign keys, and
fingerprint format checks. The PostgreSQL result store requires an application-managed
research-run verifier and `DatasetStore`; there is no row-only source fallback. Migration
`0049_unified_backtest_model.py` deleted research rows written by the retired engines and dropped
`published_research_studies.engine_contract_version`.

## Results API and bounded research submission

- `GET /api/v1/backtests` returns a bounded newest-first page of summaries: result, run,
  strategy, and dataset fingerprints, `strategy_id`, publication timestamp, and the immutable
  `summary` block (projected server-side; no ledger or equity curve). One source filter
  (`run_fingerprint`, `strategy_fingerprint`, `dataset_fingerprint`, or `strategy_id`), `limit`
  (1–100, default 50), and `offset`.
- `GET /api/v1/backtests/{result_fingerprint}` returns one reverified result (`detail=full`) or a
  bounded projection (`detail=summary`), plus the source run's `costs` (including `spread_bps`)
  and derived `metrics` as siblings outside canonical bytes.
- `GET /api/v1/backtests/{result_fingerprint}/benchmark` returns `thytrader-buy-and-hold-v1`:
  buy at the first evaluation open and sell at the `evaluation.ends_at` open, both as unified
  taker legs (taker fee, fixed slippage, half the spread stress), marking at the stressed bid
  close, with a canonical `benchmark_fingerprint` and `engine`.
- `GET /api/v1/backtests/{result_fingerprint}/metrics` returns derived
  `thytrader-performance-metrics-v1` ratios.
- `GET /api/v1/research/backtest-model` describes this model's assumptions for agents (it
  replaced the removed `engine-support` matrix).
- `POST /api/v1/backtests` takes `strategy_id`, a verified `dataset_fingerprint`, optional
  `htf_dataset_fingerprint`, `indicator_dataset_fingerprints`, `additional_instrument_datasets`,
  an evaluation period (or both dates omitted — the server fills the common covered window of
  every bound clock), `initial_quote_balance`, maker/taker fee rates, `fixed_slippage_bps`, and
  optional `spread_bps`. `?async=true` queues a research job (HTTP 202). Explicit dates the
  datasets cannot cover are `422 backtest_window_rejected`; `engine_contract_version` is a 422
  caller error. The UI may prefill fee rates from `GET /api/v1/fees` suggestions (labeled as
  suggestions); they become published cost assumptions, not observed fills.

Failures use redacted envelopes: malformed fingerprint `400 backtest_invalid`, unknown result
`404 backtest_not_found`, storage/integrity failure `503 backtests_unavailable`. Without a
database URL the routes fail closed with `503`. Decimal values remain canonical strings at the API
boundary; the browser formats them with exact string/`BigInt` arithmetic.

The Test stage run bar has no engine picker. Its Advanced options hold fixed slippage and the
optional spread stress, and a "How backtests simulate" disclosure summarizes these assumptions.

## Explicitly not modeled

- queue position, partial fills, or post-only rejection of resting limits (a touched limit fills
  completely);
- observed bid/ask data or calibration of the spread stress to venue microstructure;
- margin, leverage, borrow, or funding for spot shorts (futures runs model margin, leverage,
  funding and liquidation as above);
- cross-strategy portfolio allocation;
- the account risk policy's entry gate, including the opt-in fleet entry clustering cap and
  BTC-beta-weighted exposure cap
  ([ADR 0125](../decisions/0125-correlation-aware-risk-limits.md)): a fleet of backtests counts
  every entry, while paper and live skip entries once either cap binds;
- sensitivity analysis or walk-forward optimization inside one run (studies compose ordinary
  submissions; see [research studies](research-studies.md));
- paper broker, exchange adapters, Coinbase submission, or live execution.

Indicator warmup and first-valid-index rules: `src/thytrader/strategies/` (each indicator spec) and
[ADR 0062](../decisions/0062-research-paper-semantics-audit-stage-4.md).
