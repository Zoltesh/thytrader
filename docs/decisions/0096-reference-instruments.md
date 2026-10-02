# 0096: Read-only reference instruments (`data_requirements.reference_instruments`)

- Status: Accepted
- Date: 2026-10-02
- Amends: [0005](0005-canonical-strategy-schema.md) (`data_requirements` gains
  `reference_instruments`; indicators gain `source`),
  [0087](0087-per-bar-decision-timeline.md) (two skip reasons and reference operand labels), and
  [0089](0089-agent-research-ergonomics.md) (`bound_datasets` gains the `reference` role)
- Relates to: [0025](0025-multi-timeframe-htf-filter.md), [0041](0041-paper-live-htf-filter-evaluation.md),
  [0056](0056-multi-instrument-documents-and-pyramiding.md), [0086](0086-indicator-catalog-expansion-and-offset.md),
  [0091](0091-portfolio-deployment-limits-and-manager-proposals.md),
  [0093](0093-signal-based-exits.md)

## Context

Research on alt trend books kept finding the same thing: alts trend well while BTC is in a daily
uptrend and badly otherwise. The rule "trade alts only while BTC-USDC 1d close > EMA(100)" could not
be written down. Operands were only indicators of the traded instrument (or literals).
`additional_instruments` (ADR 0056) runs the same rules independently on each covered product. The
`htf_filter` reads the strategy's own product on a coarser clock.

## Decision

### 1. Schema

```json
"data_requirements": {
  "warmup_bars": 50,
  "required_fields": ["open", "high", "low", "close", "volume"],
  "reference_instruments": [{"id": "btc", "product_id": "BTC-USDC", "timeframe": "1d"}]
},
"indicators": [
  {"id": "btc_close", "kind": "identity", "input": "close", "source": "btc", "parameters": {}},
  {"id": "btc_ema", "kind": "ema", "input": "close", "source": "btc", "parameters": {"period": 100}}
]
```

- At most **3** references. Each `id` matches `^[a-z][a-z0-9_]{0,31}$` and is unique. No
  `product_id` + `timeframe` pair may repeat.
- A reference must use the **strategy's quote currency**. Its timeframe equals the strategy
  timeframe or is a coarser integer multiple of it: the HTF pairing rule (ADR 0040), with the
  decision clock itself allowed.
- An indicator with `source` reads that reference. It must omit `timeframe`, because it reads the
  reference's clock. `constant` takes no source. ATR stop and trail indicators, and every
  `htf_filter` indicator, read the traded instrument only. `htf_filter.data_requirements` may not
  declare references. Every `source` must name a declared reference, and every reference must be
  read by at least one indicator.
- Operands do not change. `entry.when` and `exits.signal_exit` reference sourced indicators by id
  like any other.
- Reference warmup is **derived** from its indicators (period plus offset, as for extra indicator
  clocks). `warmup_bars` keeps describing the traded instrument's decision clock, so a sweep axis
  on a reference indicator's period needs no warmup edit.
- Canonical JSON drops an empty or omitted list and an absent `source`. Documents without
  references keep their canonical bytes and fingerprints, and a golden test pins this. A declared
  reference is part of the canonical bytes, so it is part of the fingerprint.

### 2. Alignment (no lookahead)

At decision close `T`, a reference indicator reads the last reference bar whose exclusive close is
`≤ T`. This is the HTF closed-bar rule. Reference bars are filtered to those closed by `T`
**before** any indicator is computed. An in-progress reference bar can therefore never change a
value. A same-timeframe reference reads the bar that closes together with the decision bar. On a
1h alt strategy with a 1d BTC reference, every hour of day D reads BTC's day D-1 bar until day D
closes.

### 3. Backtest and research

- The kernel computes reference indicator rows once per reference series and holds them onto the
  decision-clock rows, like extra-clock indicators. Fill semantics are unchanged, so
  `SIMULATION_SEMANTICS` is not bumped. Reference-free documents produce byte-identical results.
- Reference datasets bind automatically, like the HTF and extra clocks (ADR 0089): the newest
  complete catalog dataset for each reference's product and timeframe. They appear in
  `bound_datasets` with `role: "reference"` and `reference_id`. Explicit
  `reference_dataset_fingerprints: [{reference_id, product_id, timeframe, dataset_fingerprint}]`
  pins them; each entry must equal a declared reference. A missing dataset fails closed with
  `datasets_missing`, which names the watch-add and ingest commands.
- Omitted bounds shrink to the reference's closed-bar coverage, including its warmup. Explicit
  bounds beyond that coverage are refused.
- Studies carry the bindings into every window and candidate. A cross-market study re-targets only
  the traded instrument: the reference stays fixed (BTC stays BTC). A market in a different quote
  currency from the reference is refused. Portfolio backtests bind sleeve references the same way.
- Research and paper/live call the same evaluator, so a trace and a decision row agree on the same
  bar.

### 4. Paper and live

- **Start gate.** A deployment, or a portfolio sleeve, whose strategy reads references starts only
  if every reference series is on the **enabled** market-data watchlist of the ingestion provider.
  Otherwise the start returns HTTP 409, and the message names each series and the exact
  `uv run thytrader-data watch-add --product-id … --timeframe … --lookback-hours … --confirm`
  command. The suggested lookback covers the derived warmup. The runtime lane never adds a watch
  itself, because data mutations stay in the confirmation-gated data lane. Watch-add accepts only
  enabled spot products, so this gate also enforces "enabled products".
- **Every cycle.** The worker fetches each reference's deploy-anchored closed-bar window. The
  fetch is best effort: a failed fetch yields an empty window.
- **Fail closed.** On every entry-eligible bar, each reference needs the bar that closed last at
  or before the decision close, plus a contiguous warmup window. If either is missing, the bar
  opens no risk and is journaled `skipped`:
  - `reference_data_stale` (`REFERENCE_DATA_STALE`) when the newest closed reference bar is older
    than the bar this close needs;
  - `reference_data_missing` (`REFERENCE_DATA_MISSING`) when there are no bars, a gap, or too
    little history for the warmup.

  The `summary` names the series. The bot does not pause. Stops, targets, trails, and time exits
  keep running. A signal exit that reads a reference sees undefined values and cannot match, so no
  exit is invented. Multi-instrument documents apply one gate to every covered product on the
  shared bar.
- **Decision rows.** Reference operands are labeled with the reference's base currency and clock,
  for example `BTC · EMA(100) [1d]`, with the exact values the rule read.

### 5. Authoring surfaces and template

- In the builder, "Market and data" gains a "Reference instruments" block (add up to 3; id,
  product, timeframe limited to legal clocks). Each indicator gains an "Instrument" picker
  (the traded instrument or a reference). Operand labels read "BTC · EMA(100) @ 1d". The
  plain-language summary states the gate, and the data-readiness panel lists each reference series.
- The indicator catalog reports `supports_source` per kind.
- Template `btc-regime-gate`: long when EMA(20) crosses above EMA(50), only while
  `BTC-<quote>` 1d close > EMA(100), with an ATR stop and target.

### Contract

Ops contract `thytrader-ops-contract-v56` adds `reference_instrument_runtimes` (`research`,
`paper`, `live`) and `max_reference_instruments` (3). The expected Alembic revision stays `0059`.
No migration is needed: skip reasons are stored as unconstrained text, and references live inside
the snapshot JSON.

## Not supported

- **Orders on a reference.** There are no cross-instrument orders, pairs, or spreads. A strategy
  trades one instrument, or its ADR 0056 covered products, which all share the same references.
- References in another quote currency, or on a clock finer than the decision clock.
- HTF-filter indicators on a reference. Gate in `entry.when` instead.
- Auto-adding the watch on deployment start.

## Consequences

- Regime gates on another market are expressible and identical across backtest, paper, and live.
- A lagging reference watch blocks new entries without pausing the bot. Operators see it as
  `reference_data_stale` rows, not as silence.
- A same-timeframe reference on a fast clock depends on that series arriving by the decision
  close. Coarser references tolerate ingest lag better.

## Alternatives considered

- **Let `htf_filter` name another product.** Rejected: one filter per strategy, and HTF semantics
  (a separate AND) would not let a reference appear inside `any`/`not` trees or in exit rules.
- **Operands with a `product_id`.** Rejected: indicators already own inputs, parameters, offset,
  and warmup. A per-indicator `source` keeps one place for all of it.
- **Pause the bot when a reference is stale.** Rejected: a stale reference only removes evidence
  for new entries. Skipping entries is the fail-closed answer, and the bot resumes trading on the
  next fresh bar without an operator resume.
- **Auto-add the watch at start.** Rejected: the runtime lane must not mutate data configuration
  without the data lane's confirmation.
