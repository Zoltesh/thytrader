# ADR 0111 slice completion: durable risk accounting scopes

Worktree: `/home/hermes/projects/tt-review-risk` on `feat/review-risk`.
No production mutation, no `.env` read, no `make run`, no production database.

## Interfaces

- `daily_loss_snapshots` / `counts_for_daily_loss` in `src/thytrader/risk/exposure.py`.
  Exposure helper `risk_bearing_snapshots` is unchanged.
- `evaluate_circuit_breakers` now partitions daily loss by spot quote and limits drawdown to
  the matching strategy, or a discretionary book on that product. Rate limits still use
  risk-bearing occupancy.
- `evaluate_new_entry` / `evaluate_runtime_breakers` pass the full snapshot list to breakers
  and keep exposure capital on risk-bearing books.
- Worker `_risk_snapshots` returns the daily-loss set (includes stopped flat). Discretionary
  admission loads the same statuses via `counts_for_daily_loss`.
- Optional policy fields, omitted from canonical bytes when unset:
  `max_order_quantity`, `max_order_notional_quote`, `min_available_quote_reserve`.
  Reason codes: `MAX_ORDER_QUANTITY`, `MAX_ORDER_NOTIONAL`, `BALANCE_RESERVE`.
- HTTP `PUT/GET /api/v1/risk-policy` and `thytrader-runtime set-risk-policy` accept the three
  optional flags. Compiled defaults and documents that omit them are unchanged.
- No Alembic revision. `0065` is reserved and unused. `EXPECTED_SCHEMA_REVISION` stays `0064`.

## Tests

```text
uv run pytest tests/risk tests/api/test_risk_policy.py tests/runtime_control/test_cli.py -q -k "risk or breaker or loss or policy"
106 passed, 31 deselected
uv run ruff check <changed Python files>
uv run ruff format --check <changed Python files>
uv run ty check src/thytrader/risk src/thytrader/execution/discretionary.py src/thytrader/execution/loop.py src/thytrader/execution_worker/service.py src/thytrader/api/routes/risk_policy.py src/thytrader/runtime_control/cli.py tests/risk/test_loss_scope.py
```

ty check passed. Local GitNexus `detect-changes` could not run: this worktree's
`.gitnexus` storage is foreign to the index registry. Impact was read from
`/home/hermes/projects/thytrader` before editing. `risk_bearing_snapshots` was CRITICAL
and was not modified.

Covered: unrelated drawdown (latch and fraction, same product and different product),
discretionary versus strategy, stopped flat loss retained, daily latch after stop, explicit
reset leaving same-day loss and pinned capital, UTC rollover, late fill with stale cash,
same and different quotes, mixed quote fail-closed, open book without a same-day baseline,
legacy opening-equity fallback, deleted-row absence, optional bounds unset versus set,
worker snapshot inclusion of stopped flat books.

## Limitations for lead

- Paper strategy deletion (`_delete_paper_books`) still removes fills. Account daily loss
  cannot survive that delete without a table written before deletion. Do not use migration
  `0065` unless that write is added in the deletion path. This slice did not change deletion.
- Operator `risk` payload does not yet echo the three optional bounds or the new reason codes.
  Lead owns operator schemas.
- Exposure still sums risk-bearing books in one mode without an FX conversion. Daily loss does
  not. A mixed-quote exposure cap remains a follow-up if lead wants that split too.
- GitNexus impact was read from `/home/hermes/projects/thytrader` (same original HEAD). This
  worktree's `.gitnexus` storage is foreign, so local `detect-changes` could not use an owned
  index. Do not treat that as a clean graph check. `risk_bearing_snapshots` impact was
  CRITICAL; its filter was not changed.
- No ops-contract bump. Lead integrates landing docs, operator schemas, and roadmap.
