# Slice completion: live execution evidence and backtest bar explanations

ADR: [0116](../decisions/0116-live-execution-evidence-and-backtest-bar-explanations.md)

Branch: `feat/review-evidence`. No ops-contract bump and no operator report-schema rewrite.
Those remain for the integrating lead.

**Historical initial handoff, not the final correctness assessment.** Lead review found
material defects despite the passing checks listed here: unknown-liquidity normalization,
partial exits, lifetime populations, incompatible rules, and future-close causality.
The fixes, corrected interfaces, and new verification are recorded in
[the follow-up note](2026-10-06-execution-evidence-review-corrections.md). That note supersedes
this document's initial interface/limitation descriptions.

## Resume-session review (2026-10-06)

The prior session ended on a provider credit error after writing the implementation but
before verification. This session re-reviewed the whole diff and fixed:

- `src/thytrader/execution/execution_quality.py`: hoisted the undocumented function-local
  `IntentPurpose` import to module scope (no cycle exists); replaced wall-clock fallbacks in
  `_trip_response`/`_open_cycle_response` with explicit guards so report fingerprints stay
  deterministic for closed and open cycles alike.
- `src/thytrader/operator/cli.py`: `execution-quality --deployment-id` is now parsed as a
  UUID before URL interpolation (malformed values get a client-side error instead of a
  corrupted request path).
- `tests/api/test_backtest_bar_explanations.py`: reformatted.

Already-present shared-contract edit, kept per resume instructions: this slice appends the
ADR 0116 entry to the global `docs/decisions/README.md` ADR index. No global
`OPS_CONTRACT_ID`, `EXPECTED_SCHEMA_REVISION`, operator schema JSON, or migration-reservation
file is touched; the lead owns those for the integrated release.

## Interfaces

- `GET /api/v1/deployments/{deployment_id}/execution-quality`
  - Schema `thytrader-execution-quality-v1`
  - Closed round trips: `fill_price_pnl_before_fees`, `entry_fees`, `exit_fees`, `net_pnl`
  - `slippage_bps` is null without a journaled close; `liquidity` is null unless the order
    kind is post-only (`maker`) or marketable (`taker`)
  - `evidence.complete` is false when any reason is present
  - `totals.ledger_realized_delta` discloses ledger fee-allocation rounding
- `GET /api/v1/deployments/{deployment_id}/execution-quality/twin`
  - Schema `thytrader-execution-twin-comparison-v1`
  - 404 `execution_twin_not_linked` when no explicit link exists
  - `comparable: false` with reasons when evidence is incomplete or fill windows do not overlap
  - `fee_normalization` is counterfactual; it does not rewrite `net_pnl`
- `GET /api/v1/backtests/{result_fingerprint}/bar-explanations`
  - Schema `thytrader-backtest-bar-explanation-v1`
  - Query: `limit` 1-500 (default 100), `cursor`
  - Provenance: result, run, strategy, dataset, and signal-trace fingerprints
  - `outside_trace` holds fills whose candle is not an evaluated signal bar, including
    `evaluation_end` liquidation
  - 503 `bar_explanations_unavailable` when the re-evaluated trace does not match the result
- CLI, both read-only and HTTP-only:
  - `uv run thytrader-operator execution-quality --deployment-id UUID [--twin]`
  - `uv run thytrader-research explain-bars --result-fingerprint sha256:… [--limit N] [--cursor C]`
- UI:
  - `/deployments/{id}/execution-quality`
  - Bot detail links to that page
  - Backtest detail includes bar explanations

## Modules

- `src/thytrader/execution/execution_quality.py`
- `src/thytrader/api/routes/execution_quality.py`
- `src/thytrader/research/bar_explanations.py`

Shared edits are limited to router registration, the backtests read route, and narrow CLI
parser/handler additions.

## Tests

Hermetic only. No production database.

- `tests/execution/test_execution_quality.py`
- `tests/api/test_execution_quality_api.py`
- `tests/api/test_backtest_bar_explanations.py`
- `tests/research/test_bar_explanations.py`
- `tests/research/test_explain_bars_cli.py`
- `web/src/lib/executionQuality.spec.ts`
- `web/src/lib/barExplanations.spec.ts`

## Verification results (resume session)

- `uv run pytest` — 2794 passed, 89 skipped (all PostgreSQL-integration skips; hermetic).
- `uv run ruff check .` / `uv run ruff format --check .` / `uv run ty check` — clean.
- `web`: vitest 435 passed (47 files), `svelte-check` 0 errors/0 warnings, prettier+eslint
  clean, `npm run build` succeeds.
- GitNexus: the copied worktree index was foreign (unusable, moved to
  `.gitnexus.foreign-stale`). Read-only impact ran against the root checkout's index
  (`/home/hermes/projects/thytrader`, built at ca97364, one commit ahead of this branch's
  base 901a059): `create_app` LOW; operator `_parser` LOW (an UNKNOWN same-name candidate
  in another module was resolved by diff inspection — the change is a pure subcommand
  append); `evaluate_result_signal_trace` LOW (read-only reuse). `ledger_from_snapshot`
  is CRITICAL/29-callers: this slice only calls it in a new module and does not modify it.
  A fresh local graph `tt-review-evidence` was rebuilt with `--workers 1
  --embedding-threads 1 --index-only --pdg --embeddings`; `detect-changes` ran on it
  (see the commit-message or review summary for the outcome).

## Limitations

- Decision-journal reads are capped at 25 pages of 200 rows per product. Older fills then
  lack closes and the report includes `decision_coverage_limited`.
- A fill that over-covers a cycle is clamped to the remaining quantity. Its full recorded
  fee stays on that exit and `position_flip_fill` is set. The excess quantity is not opened
  as a new cycle, matching the fill ledger's flatten behavior.
- Twin comparison does not pair individual signals. It reports entry-fill counts, fill
  windows, and divergence reasons.
- `execution-quality` and `explain-bars` are HTTP-only. `--local` is rejected.
- Ops contract version and operator report schemas are unchanged in this slice.
