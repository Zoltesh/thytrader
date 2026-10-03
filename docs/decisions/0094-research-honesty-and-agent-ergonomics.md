# 0094: Research honesty and agent ergonomics

- Status: Accepted (the `create --file` with sleeves deferral is superseded by
  [0101](0101-atomic-portfolio-creation-with-sleeves.md))
- Date: 2026-10-02
- Amends: [0044](0044-parameter-sweeps-wfo-stitched-equity.md) (study summaries carry axis
  values, per-candidate sums, and a thinned stitched path), [0082](0082-strategy-root-mutable-strategies-auto-snapshots.md)
  (one validation shape and document issue paths), [0087](0087-per-bar-decision-timeline.md)
  (operand rounding and crossover wording), and [0088](0088-portfolio-model-and-portfolio-backtest.md)
  (at most 32 sleeves; batch sleeve adds)
- Relates to: [0019](0019-ops-contract-identity.md), [0030](0030-agent-e2e-primary-surface.md),
  [0083](0083-unified-backtest-model.md), [0089](0089-agent-research-ergonomics.md),
  [0090](0090-research-correctness-optional-take-profit-diagnostics.md),
  [0091](0091-portfolio-deployment-limits-and-manager-proposals.md)

## Context

A full day of an agent (Claude Code) and Hermes driving ThyTrader through its skills found
results that were easy to misread and workflows that cost dozens of calls:

1. **Results did not state their window.** A backtest with omitted bounds starts after the
   strategy's own warmup, so on the same BTC-USDC 1d dataset buy-and-hold read 48% (warmup 60)
   and 129% (warmup 110). Summaries showed only `evaluation_bars`.
2. **Study summaries could not be read alone.** `window_pnl` rows had no candidate parameters or
   bounds, only the selected WFO path was aggregated, and `stitched_oos_equity.points` was empty
   while `point_count` was 1081. Reading one WFO took 8 `show-snapshot` calls plus `plan-study`.
3. **Validity hid in two shapes.** `import-strategy` / `save-strategy` printed top-level `valid` /
   `issues`, `show-strategy` nested them under `validation`; an agent parsing one shape treated an
   invalid import as usable.
4. **Issue paths were validator internals**:
   `entry.when.AllCondition.all.0.function-after[validate_cross_operands(), ComparisonCondition].left.IndicatorOperand.input`,
   plus a dozen errors from union members the document never meant.
5. **`fixed_slippage_bps: 5` was a 422**: decimal request fields took strings only.
6. **Decision rows were noisy and a false cross was unexplained**: operand values carried 60+
   digits, and "did not cross above" did not say that fast was already above slow.
7. **Portfolio CLI gaps**: no delete, `create` without limits or manager settings, one revision per
   sleeve, and a 20-sleeve cap (11 majors on two clocks need 22) reported only at the 21st add.
8. **Mirroring 12 strategies took 24 extra calls** (clone, then save to rename).
9. **The operator `portfolio` report logged a Coinbase 404 ERROR on every call** for a dust asset
   with no USD market, although the report already listed it in `unvalued_assets`.
10. **One day of research created 1,332 per-market strategies** with no way to list or delete them
    as a group.

## Decision

**Every backtest result states its window, outside the result bytes.** `BacktestEvaluationWindow`
(`timeframe`, `evaluation_start`, `evaluation_end` (exclusive), `first_evaluated_bar`,
`last_evaluated_bar`, `evaluation_bars`, `warmup_bars`, `warmup_start`) is derived at read time from
the result's verified run (`backtest_evaluation_window`). It appears as `window` on
`GET /api/v1/backtests/{fp}` (summary and full), on each `GET /api/v1/backtests` entry (the listing
joins the run), on operator `performance`, in `thytrader-research show-result`, flattened into
`list-results` rows, and under the Test stage metrics. It is `null` when the run cannot be read.
Canonical result bytes and every fingerprint are unchanged. Skills and the UI tell agents to pin
`evaluation_start` / `evaluation_end` when comparing strategies.

**Study summaries explain every row.** Each `window_pnl` row adds `product_id`, `evaluation_start`,
`evaluation_end`, `axis_values`, and `total_return_fraction`. `axis_values` is recovered from the
candidate snapshots: the sweepable coordinates (labelled like derived candidate names:
`fast.period`, `sizing.risk_fraction`, `exits.initial_stop_multiple`, `entry_literal.rsi.literal`)
whose values differ across the study's candidates; `{}` for a single candidate. `candidates[]`
sums every window per candidate: in-sample and out-of-sample PnL, positive OOS windows, OOS
trades, selected windows, and full-window PnL for sweeps and cross-market legs (named
`full_window_*`, never `oos_*`, per ADR 0044). The summary returns the stitched OOS path thinned to
at most 200 marks the way the portfolio chart is (first, last, and each bucket's low and high),
with the full `point_count` and `stitched_oos_points_downsampled`; `?detail=full` still has every
mark. The UI study view shows axis values, bounds, and the per-candidate table. The canonical
study document and its fingerprint are unchanged.

**One validation shape.** Strategy write commands print `validation: {valid, issues, warnings}`
exactly as `show-strategy` does; the top-level copies stay for one release, deprecated. When the
result is invalid the CLI also prints one stderr line, `… as an INVALID draft (N issues): <loc>:
<message>`.

**Issue paths are document paths.** `strategies.issue_paths.document_issues` walks the submitted
document along each Pydantic location, drops union-member tags (class names, `function-after[…]`,
discriminator values, boolean tags), keeps only the union members whose shape matches the document
(fewest missing or unknown keys at that position), and folds equally bad members into one
alternatives message. Example: `entry.when.all[0].left.input: unknown field "input"` and
`entry.when.all[0].left: must be an indicator operand (needs "indicator") or a literal operand
(needs "literal")`. Messages drop Pydantic phrasing (`Value error, `, `Input should be`). HTTP,
CLI, and UI all read these stored issues; drafts saved earlier keep their old text until re-saved.
Validity itself is unchanged.

**Decimal request fields accept JSON numbers.** Backtest and study starts (costs,
`oos_fraction`), sweep `parameter_axes[].values`, and portfolio backtest costs take a number or a
string. A number becomes the canonical plain decimal string (`5` → `"5"`, `1e-05` → `"0.00001"`,
read through its shortest round-trip text) before any fingerprint; strings pass through unchanged,
so `5` and `"5"` give identical request, execution, and run fingerprints and existing fingerprints
do not move. Booleans and non-finite numbers are 422.

**Decision rows read cleanly.** Leaf operand `value` / `previous_value` are rounded half-even to 12
significant digits at write time; the exact values the runtime compared stay in
`rule.signal.indicator_values`. A false crossover names where the lines are: `EMA(9) is above
EMA(21); no new cross this bar (…)`, or `… is below …; no cross above yet (…)`.

**Portfolio CLI.** `thytrader-portfolio delete --portfolio-id --revision [--dry-run] --confirm`
wraps the existing revision-guarded `DELETE` (refused while any sleeve runs or is paused; strategies
are kept). `create` takes the limit, mandate, and manager-permission flags of `update`, or
`--file` with the `POST /api/v1/portfolios` body, all in its one revision. New
`POST /api/v1/portfolios/{id}/sleeves/batch` (`add-sleeves --file`) adds up to 32 sleeves
atomically in one revision with one `sleeve_added` journal entry each, applying every single-add
check to the whole batch. `MAX_SLEEVES` is 32 (nothing in the UI, risk book, or backtests depended
on 20; the portfolio backtest's pairwise report grows to at most 496 pairs); the cap is in the
skill and in `create` / `add-sleeve` / `add-sleeves --help`. The skills name the `deployment` field
on `portfolio-status` / `deployment` sleeve rows.

**`clone-strategy --name`** (`POST /api/v1/strategies/{id}/clone` with an optional `{"name"}`
body) names the copy in the same call.

**Unsupported USD products are cached per process.** The Coinbase adapter remembers products that
answered 404 for the process, logs one INFO line, and suppresses the SDK's own ERROR line for that
expected 404; the asset stays in `unvalued_assets`. Other failures still raise and log.

**Strategy library hygiene.** `GET /api/v1/strategies?tag=` (JSONB containment on
`metadata.tags`), `thytrader-research list-strategies --tag`, and tag chips plus an active filter
chip in the web Strategies library. Library rows carry `tags`. `bulk-delete-strategies --tag
… --dry-run|--confirm` pages the tagged library and sends batches of 100 to the existing bulk
route, so its safety is unchanged (running or paused bots block their strategy; live ledgers are
kept); it fails closed, deleting nothing, if a listed row lacks the tag. Cross-market and sweep
variants remain snapshots of their base strategy and never appear as library rows (pinned by a
test).

**Contract.** Ops contract `thytrader-ops-contract-v54` adds `research_honesty`
(`result_window`, `study_axis_values`, `study_candidate_aggregates`, `study_stitched_points`,
`document_issue_paths`, `json_number_decimals`), `strategy_library` (`tag_filter`,
`bulk_delete_by_tag`, `clone_name`), `portfolio_max_sleeves: 32`, and
`portfolio_sleeve_operations: ["batch_add"]`, so a CLI never sends `--tag` to an image that would
ignore it. Alembic stays `0058` (from ADR 0093); no schema change.

## Consequences

- Agents compare strategies on stated windows and see why two headline returns differ.
- One `show-study` call is enough to judge a sweep or WFO, including how the losing candidates
  did out of sample.
- Invalid drafts are hard to miss, and their issues point at the field to fix.
- Bulk research is cheaper: batch sleeves, named clones, tag filters, and tag-scoped deletes.
- Study summaries load each candidate snapshot (at most 64) to recover axis values; an unreadable
  snapshot leaves that candidate's `axis_values` empty instead of failing the summary.
- Tests: `tests/strategies/test_issue_paths.py`, `tests/research/test_decimal_inputs.py`,
  `tests/research/test_study_summary.py`, `tests/api/test_backtests.py` (window),
  `tests/api/test_strategies_api.py` (tags, variants, clone name), `tests/api/test_portfolios_api.py`
  (batch, 32-sleeve cap), `tests/research/test_mutation_cli.py`, `tests/portfolios/test_cli.py`,
  `tests/execution/test_decision_rules.py`, `tests/exchanges/test_coinbase.py`, and the web specs.

## Alternatives considered

- **Add the window to `BacktestSummary`.** Rejected: it would change canonical result bytes and
  every new result fingerprint for unchanged inputs.
- **Store axis assignments in the study document.** Rejected: it changes study fingerprints; the
  candidate snapshots already carry the assignment.
- **Make strategy unions discriminated to get clean paths.** Rejected for now: it changes how
  every stored document validates; rewriting the error report fixes the explanation without
  touching validity.
- **Quantize every decision value, including `signal`.** Rejected: the signal record is the exact
  evidence of what the runtime compared; only the human-facing operands are rounded.
- **`create --file` with sleeves.** Deferred: creating a portfolio and its sleeves in one revision
  needs a combined planner; `create` then `add-sleeves --file` is two calls and two revisions, each
  atomic.
  [ADR 0101](0101-atomic-portfolio-creation-with-sleeves.md) subsequently ships this as one
  transaction at revision 1.
- **Server-side bulk delete by tag.** Rejected: the existing per-strategy bulk route and its safety
  checks are reused unchanged; the CLI only resolves the ids.
