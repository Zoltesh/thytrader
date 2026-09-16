# 0052: Richer sweep axes and persisted research-study catalog

- Status: Accepted
- Date: 2026-09-16
- Relates to: [0007](0007-immutable-research-run-specifications.md),
  [0012](0012-operator-diagnostics.md),
  [0019](0019-ops-contract-identity.md),
  [0035](0035-phase-11-research-rigor.md),
  [0044](0044-parameter-sweeps-wfo-stitched-equity.md),
  [0046](0046-shipped-vs-remaining-0031-destination.md),
  [0047](0047-wider-fail-closed-indicator-catalog.md),
  [0048](0048-paper-deploy-fee-fields.md),
  [0049](0049-experiential-train-v1.md),
  [0050](0050-daily-loss-drawdown-rate-collars.md),
  [0051](0051-in-app-operator-chat.md)

## Context

[ADR 0044](0044-parameter-sweeps-wfo-stitched-equity.md) shipped parameter sweeps, walk-forward
optimization, and derived stitched OOS equity as research composition. Axes were indicator-parameter
only. Composed study documents were returned from `submit-study` but not stored as operator-visible
catalog rows. The roadmap Research destination row and
[ADR 0046](0046-shipped-vs-remaining-0031-destination.md) still named that leftover.
[ADR 0047](0047-wider-fail-closed-indicator-catalog.md) shipped a wider fail-closed indicator
catalog and is unchanged by this slice.
[ADR 0048](0048-paper-deploy-fee-fields.md) shipped paper deploy maker/taker fee fields.
[ADR 0049](0049-experiential-train-v1.md) shipped fail-closed experiential trainer V1.
[ADR 0050](0050-daily-loss-drawdown-rate-collars.md) shipped daily-loss / drawdown breakers,
order-rate limits, and reference-price collars on ops contract `thytrader-ops-contract-v18` and
Alembic `0030`. [ADR 0051](0051-in-app-operator-chat.md) shipped loopback in-app operator chat.
This slice does not revert those.

WFO in-sample selection, child V1/V2/V3 fingerprints, complete-only datasets, and no interpolation
stay in force. This slice does not rewrite fold geometry, peek at OOS to pick a winner, train
models, or add exchanges.

## Decision

Keep contract version `thytrader-research-study-v1`. Extend `parameter_axes` with an optional
`target` (`indicator` default, omitted from canonical JSON so ADR 0044 indicator-only request
fingerprints stay stable):

| Target | Locator | Legal `parameter` names |
|---|---|---|
| `indicator` | `indicator_id` | `period`, `fast_period`, `slow_period`, `signal_period`, `k_period`, `d_period`, `stdev_multiplier`, `value` |
| `sizing` | none | `risk_fraction`, `min_quote_notional`, `max_quote_notional` |
| `exits` | none | `initial_stop_multiple`, `take_profit_multiple`, `trailing_stop_multiple`, `max_bars_held` |
| `execution` | none | `max_entry_wait_bars` |
| `entry_literal` / `htf_literal` | `indicator_id`, optional `condition_operator` | `literal` |

Cartesian product remains at most 8 candidates across 1–4 axes. Product id and decision timeframe
are not sweepable. Derived documents still copy the base, substitute the named field, raise
`warmup_bars` when periods require it, and take a deterministic UUIDv7 `strategy_id`. Indicator-only
cells keep the ADR 0044 fingerprint 3-tuple `(indicator_id, parameter, value)`.

Persist each assembled study after submit in `published_research_studies` (Alembic `0031`, revises
`0030`). The row stores the catalog summary plus canonical study JSON. Repeating an identical
submit is idempotent when the canonical bytes match. PostgreSQL is the durable catalog. The API
process without a database uses a process-local in-memory catalog. Operator `--local` without
PostgreSQL is `STUDY_CATALOG_UNAVAILABLE` degraded, not an empty healthy list.

Read surfaces:

- `uv run thytrader-research list-studies` / `show-study` (read-only; `show-study` omits child
  windows and stitched equity points)
- `GET /api/v1/research/studies` and `GET /api/v1/research/studies/{study_fingerprint}`
- `uv run thytrader-operator studies` / `GET /api/v1/operator/studies` (`report_kind=studies`)

Ops contract becomes `thytrader-ops-contract-v19` with `expected_schema_revision` `0031`. Paper
deploy fee fields from ADR 0048, experiential-model engines from ADR 0049, and risk breakers /
order-rate limits / reference-price collars from ADR 0050 stay on the contract. In-app operator
chat from ADR 0051 is unchanged. WFO selection remains in-sample only. Embargo gaps and overlapping
OOS are still not interpolated.

## Consequences

- Operators can list submitted studies without scraping logs or child result ledgers.
- Agents can sweep sizing, exits, execution wait bars, and comparison literals without inventing
  grid math or rewriting entry/exit operators.
- Derived publications remain deployable fingerprints; deploying them is still a separate runtime
  action.
- Extra exchanges and multi-instrument strategy documents stay out. Experiential training remains
  the ADR 0049 lane. In-app chat remains the ADR 0051 lane.

## Alternatives considered

- **Rewrite WFO to select on OOS:** rejected; that is lookahead.
- **Interpolate missing candles or embargo equity:** rejected; complete-only.
- **Skip the ops-contract bump because research is composition:** rejected; this slice adds Alembic
  `0031` and an operator report kind.
- **Treat a missing database as an empty healthy catalog:** rejected; that hides unavailable
  storage.
