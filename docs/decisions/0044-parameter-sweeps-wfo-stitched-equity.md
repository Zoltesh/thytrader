# 0044: Parameter sweeps, walk-forward optimization, and stitched OOS equity

- Status: Accepted
- Date: 2026-09-16
- Relates to: [0005](0005-canonical-strategy-schema.md),
  [0007](0007-immutable-research-run-specifications.md),
  [0009](0009-deterministic-bar-level-backtest-engine.md),
  [0031](0031-coinbase-first-platform-end-state.md),
  [0035](0035-phase-11-research-rigor.md)

## Context

Phase 11 ([ADR 0035](0035-phase-11-research-rigor.md)) shipped OOS holdout, walk-forward
**validation**, and cross-market studies. Walk-forward validation freezes one published strategy
fingerprint on every fold. Aggregates are equal-weight window statistics, not a continuous equity
curve.

The product destination still names parameter sweeps / walk-forward optimization (WFO) and stitched
multi-window equity. ADR 0035 correctly forbade auto-tuning **as authoring** and refused to pretend
that a new fingerprint each fold is "one published strategy." This slice keeps that authoring
boundary and adds a research-only composition layer that selects among published (or
submit-published derived) fingerprints.

Complete-only datasets, no interpolation, no lookahead, and child V1/V2/V3 fingerprints remain the
evidence. This slice does not change YOLO, playbook, extra exchanges, shorting, or attached entry
brackets.

## Decision

Keep contract version `thytrader-research-study-v1`. Add two study kinds. Existing `walk_forward`
validation is unchanged. New request fields are omitted from canonical JSON when empty so Phase 11
request fingerprints stay stable.

### Parameter sweep (`parameter_sweep`)

One half-open evaluation window. Each candidate is one child backtest of a published strategy
fingerprint that shares the base document's product and decision timeframe.

Candidates come from **exactly one** of:

- `candidate_strategy_fingerprints`: 2–8 already-published fingerprints;
- `parameter_axes`: 1–4 axes, each naming an existing indicator id and a legal parameter
  (`period`, `fast_period`, `slow_period`, `signal_period`, `stdev_multiplier`, `value`) with 2–8
  unique values. Cartesian product size is at most 8.

Axes do not rewrite entry/exit logic. Derived definitions copy the base document, substitute those
parameters, raise `warmup_bars` when the new periods require it, and take a deterministic UUIDv7
`strategy_id` so the canonical fingerprint is reproducible. `plan-study` derives in memory and does
not persist. `submit-study --confirm` publishes missing derived documents through the existing
publication store, then submits ordinary backtests. Derived publications are deployable fingerprints
like any other; deploying them is a separate runtime action.

### Walk-forward optimization (`walk_forward_optimization`)

Same fold geometry as `walk_forward` (rolling or anchored `in_sample_bars`, `out_of_sample_bars`,
`step_bars`, optional `embargo_bars`, at most 24 folds). Each fold evaluates **every** candidate on
the in-sample window. Selection uses only in-sample `selection_metric`
(`total_return_fraction` or `total_net_pnl` maximize; `maximum_drawdown_fraction` minimizes). Ties
break on lexicographically smaller strategy fingerprint. The matching out-of-sample child is the
honest fold score. Non-selected OOS children are still simulated (the plan is a full grid) and are
not the performance claim.

At most 128 child windows. In-sample selection cannot see OOS results.

### Stitched OOS equity

When two or more scored OOS windows are time-ordered and non-overlapping, the study document may
include a **derived** stitched equity curve. Each child still starts from `initial_quote_balance`
and liquidates at its window end. Stitching compounds per-window equity **returns** in chronological
order. Embargo gaps are not interpolated. Overlapping OOS (`step_bars` < `out_of_sample_bars`)
refuses stitching and keeps the existing overlap warning.

This applies to `walk_forward` validation OOS windows and to **selected** WFO OOS windows. It does
not apply to parameter sweeps (same window, different fingerprints) or to a single OOS holdout
window.

### Persistence and engines

No Alembic revision. No ops-contract bump (research composition only; paper/live clocks, engines,
and schema revision are unchanged). Child run/result fingerprints remain the append-only evidence.
Repeating an identical submit reuses children.

## Consequences

- Agents can `plan-study` / `submit-study --confirm` for sweeps and WFO through
  `thytrader-research` without inventing grid math or peeking at OOS to pick parameters.
- Strategy authoring still represents one human-chosen parameter set. Sweeps are a separate
  research activity that can manufacture overfit results; honest claims use selected OOS and
  disclose stitched vs equal-weight aggregates.
- Derived sweep publications are real immutable strategies. Operators who deploy them do so
  explicitly.
- Extra exchanges, shorting, attached entry brackets, and intra-strategy pyramiding stay out.
  YOLO and playbook are unchanged by this slice.

## Alternatives considered

- **Refit by mutating one strategy_id version per fold without publishing:** rejected; child
  backtests bind published fingerprints and the run spec would lie.
- **Carry open positions across OOS windows in one engine run:** rejected; that is a new engine, not
  composition, and would mix lookahead-shaped state with fold selection.
- **Interpolate equity across embargo gaps:** rejected; complete-only, no interpolation.
- **Auto-tune inside `save-draft` / authoring:** rejected by ADR 0005 / 0035; this slice does not
  reopen that.
- **Bump ops contract:** rejected; this slice does not change paper/live clocks, engines, or the
  expected schema revision.
