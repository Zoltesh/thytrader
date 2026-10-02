# 0093: Signal-based exits (`exits.signal_exit`) across backtest, paper, and live

- Status: Accepted
- Date: 2026-10-02
- Amends: [0005](0005-canonical-strategy-schema.md) (`exits` gains an optional exit rule),
  [0083](0083-unified-backtest-model.md) (bar ordering gains a close-time signal exit),
  [0087](0087-per-bar-decision-timeline.md) (`exit_reason: signal` and the evaluated exit rule),
  and [0090](0090-research-correctness-optional-take-profit-diagnostics.md) (diagnostics count
  exits by reason)
- Relates to: [0036](0036-phase-13-live-extras.md), [0045](0045-spot-shorting-and-attached-entry-brackets.md),
  [0058](0058-protection-lifecycle-accounting.md), [0091](0091-portfolio-deployment-limits-and-manager-proposals.md)

## Context

Research at the account's real Coinbase fees (maker 0.5%, taker 0.9%) found one robust edge: long
trend-following. A daily EMA cross with an ATR trailing stop stayed positive across every parameter
neighbour on 11 markets and out of sample in walk-forward on 6 of 8. A live 12-sleeve trend
portfolio now trades it.

The only exits were the stop, the take-profit (optional since ADR 0090), the ATR trail, and the
time exit. The canonical trend form, "hold while the fast EMA is above the slow EMA and exit on the
reverse cross", could not be expressed. Trend books therefore left trends on a trail or a time cap,
and capital sat about 90% idle.

## Decision

### 1. Schema: optional `exits.signal_exit`

```json
"exits": {
  "initial_stop": {"kind": "atr_multiple", "atr_indicator": "atr", "multiple": "3"},
  "take_profit": {"kind": "none"},
  "trailing_stop": {"enabled": true, "kind": "atr_multiple", "atr_indicator": "atr", "multiple": "5"},
  "time_exit": {"max_bars_held": 1000},
  "signal_exit": {"when": {"all": [{"left": {"indicator": "fast"},
    "operator": "crosses_below", "right": {"indicator": "slow"}}]}}
}
```

- `when` is a condition tree with the `entry.when` grammar (`all`/`any`/`not`, depth 4, 64 nodes)
  and the same operand rules: decision-list indicators only (per-indicator `timeframe` and `offset`
  included), `series` required on multi-output kinds and forbidden on single-output kinds, and
  crossovers between two indicators. HTF-filter indicators are rejected because the HTF filter
  gates entries only. Unknown keys are rejected.
- `signal_exit` is omitted from canonical JSON when absent or `null`. Every earlier document keeps
  its bytes and fingerprint (pinned by golden tests). `schema_version` stays `1.0`. Adding or
  editing the rule changes the strategy fingerprint, so results and bots bind the exact rule.
- `initial_stop` stays mandatory. The protective stop, the optional trail, the optional
  take-profit, and the time exit all still apply. The first to trigger closes the position.

### 2. Semantics shared by every runtime

The exit rule is evaluated on every closed bar while a position is open, and **never on the fill
bar**. On the fill bar the stop is the only eligible exit, as before. On later bars the order is:

1. the protective stop (including a ratcheted trail) on the bar's extreme;
2. a resting take-profit touched inside the bar;
3. the exit rule at the close: a match sells as a taker at that close;
4. the time exit at the close.

A stop or take-profit that the bar touched therefore wins over a match at the close. That is the
required same-bar precedence: the protective stop first. When the exit rule and the time exit are
due on the same close they sell at the same price, and the exit is labeled `signal`.

### 3. Order type: a taker exit at the signal bar's close (the time-exit convention)

We chose a marketable taker exit, priced like the time exit, over maker-limit-then-taker:

- **Same leg as the time exit.** Backtest and paper price it at the signal bar's close with the
  taker fee, `fixed_slippage_bps`, and half the spread stress. Live cancels protection and sends
  the same marketable cover the time exit and flatten send. No new order lifecycle exists anywhere.
- **It is the "next bar open" option.** Coinbase spot trades 24/7, so the signal bar's close and
  the next bar's open are the same instant. Live sells right after the close. Paper fills the
  moment the bar closes, at the last observed trade, so backtest and paper agree exactly. A pure
  next-open fill in the backtest would force paper to hold a pending exit across a bar it can only
  see once that bar closes, which live does not do. The two prices differ by noise that slippage
  and spread stress already cover. Results disclose the assumption as the validity code
  `signal_exit_at_close`.
- **Maker exits are adversely selected here.** A trend exit fires when price has turned. A resting
  sell fills only if price comes back to it. Otherwise the book stays open into the reversal. A
  maker exit would also need a wait/fallback state machine in three runtimes and a new optimistic
  backtest assumption (touch-fills on exits). The 0.4% fee saving at Intro-tier rates does not pay
  for that risk.

### 4. Backtest and research

- Exit reason `signal` in trade lists (`BacktestExitFill.reason`).
- `thytrader-backtest-diagnostics-v1` gains `exit_reasons[{reason, count}]`: closed trades per
  reason, summing to the trade count. Rows recorded before this ADR report `exit_reasons: null`.
  Diagnostics stay outside canonical result bytes, as in ADR 0090.
- Signal-trace records gain `exit_condition` (`matched`/`not_matched`/`undefined`) on every bar
  when the strategy declares a rule. It is omitted otherwise, so older traces keep their
  fingerprints.
- Validity limit `signal_exit_at_close` appears only when the strategy declares a rule.
- **No `SIMULATION_SEMANTICS` bump.** No existing document's fills change: the new branch runs only
  for a declared rule. A document with a rule has a new strategy fingerprint, so it can never reuse
  a result computed before this change. The pinned golden result fingerprints are unchanged.
- Determinism: the rule reads the same Decimal indicator values as the entry rule.

### 5. Paper and live

- A match persists the UTC start of the signal bar on the position row
  (`execution_positions.signal_exit_bar`) **before** any order is touched. While the marker is set,
  every cycle keeps exiting (`_protect_open_position` on closed bars, `_due_exit_purpose` between
  bars) and never rests new protection. The marker survives restarts and disappears with the
  position.
- The exit reuses the marketable-exit path and the PR #124 protections. It adopts the entry's
  venue-attached child, applies a filled exit whose fills lag instead of resending, cancels every
  working order first (the attached bracket, the OCO, or the ADR 0090 stop-only `stop_limit`), and
  waits without pausing on an accepted-but-unfinished cancel. It latches repeated identical
  rejections. Intent purpose `signal_exit` (why-trade signal kind `signal_exit`).
- Paper applies its synthetic stop first on the same bar, then fills the exit at the close.
- The rule is not evaluated over a gapped window. An evaluation failure, such as missing
  indicator-timeframe coverage, is journaled and pauses a running book, like a failed entry
  evaluation. The stop keeps guarding the position. Pause and managed shutdown keep evaluating
  because exits reduce risk. A stopped book loads the extra-timeframe windows its rule needs. A book
  that is exiting is not trailed.
- Portfolio sleeves (ADR 0091) need nothing extra. Once flat, the sleeve's sizing cash
  (`live_sizing_cash`) is its full allocation again.
- `GET /api/v1/deployments/{id}` positions report `signal_exit_bar` (null when no exit is pending).

### 6. Decision timeline

Rows carry `exit_rule` (`outcome`, evaluated `condition` tree) on every bar the rule was evaluated.
A holding bar's summary ends `· exit rule: <first unmet leaf>`. The matched bar is `outcome: exit`,
`exit_reason: signal`, `reason_code: EXIT_SIGNAL`, summary `Exit (signal): <leaf> → sell …`. This
holds even while a live cancel is still pending. In that case the fill lands in a later bar's
window.

### 7. Authoring surfaces and template

- The strategy workspace's exits section has an optional "Exit when" rule tree. It reuses the entry
  condition builder, defaults to the mirror of a single-cross entry, and explains that the
  protective stop still guards the position. Summaries and snapshot diffs describe the rule in
  plain language. The server's strategy summary appends `exit when …`.
- Template `ema-trend-hold`: long when EMA(20) crosses above EMA(100), exit when it crosses back
  below. It uses a 3× ATR initial stop, no take-profit, an optional wide 5× ATR trail, and a
  1000-bar cap. It appears in `list-templates`, `show-template`, `create-strategy`, and the
  browser's New-strategy picker.

### Contract

Ops contract `thytrader-ops-contract-v53` with `signal_exit_runtimes` (`research`, `paper`,
`live`) and expected Alembic revision `0058`. Migration 0058 adds
`execution_positions.signal_exit_bar` and admits `signal_exit` in `ck_trade_reason_purpose` and
`ck_trade_reason_signal_kind`. Its downgrade refuses while a marked position or a `signal_exit`
why-trade row exists.

## Consequences

- Trend strategies can hold a trend until it reverses, with the protective stop and trail still in
  place.
- A signal exit always pays the taker fee and slippage. Research must keep using the account's
  real taker rate.
- A crossover exit is a one-bar event. The durable marker is what guarantees it is not lost when a
  venue cancel is slow, the worker restarts, or the cancel completes between bars.
- Strategies without a rule are byte-for-byte unchanged in every runtime.

## Alternatives considered

- **Maker-limit-then-taker exits.** Rejected (see 3).
- **Fill backtest signal exits at the next bar's open.** Rejected: paper could not reproduce it
  without holding an exit across an unseen bar, and on a 24/7 venue it is the same instant.
- **Re-evaluate the rule between bars instead of a marker.** Rejected: a crossover holds on one
  bar only, between-bar cycles do not load extra-timeframe windows, and a restart would forget the
  pending exit.
- **Keep the marker on the deployment row.** Rejected: multi-instrument documents need one per
  product book. The position row scopes it exactly and clears it when flat.
- **Let the exit tree read HTF-filter indicators.** Deferred: the HTF filter is an entry gate, and
  per-indicator `timeframe` already gives the exit rule coarser clocks.
- **Make the initial stop optional when a rule exists.** Rejected: the protective stop is the
  capital guard while the rule waits for a close.
