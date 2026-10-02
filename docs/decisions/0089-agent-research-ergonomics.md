# 0089: Agent research ergonomics: auto-bound datasets, market variants, async sweep budgets

- Status: Accepted
- Date: 2026-10-02
- Amends: [0044](0044-parameter-sweeps-wfo-stitched-equity.md) and
  [0052](0052-richer-sweep-axes-study-catalog.md) (the 8-candidate cap now applies to synchronous
  submits only), [0069](0069-async-backtest-jobs-study-summary.md) (async studies are planned
  before they are queued)
- Relates to: [0019](0019-ops-contract-identity.md), [0030](0030-agent-e2e-primary-surface.md),
  [0071](0071-usdc-spot-quote-markets.md), [0082](0082-strategy-root-mutable-strategies-auto-snapshots.md),
  [0083](0083-unified-backtest-model.md), [0085](0085-fast-research-ingest.md)

## Context

An agent researching strategies across 15 USDC markets (2026-10-02) hit the same friction on
every step:

1. `submit-backtest` and studies required `dataset_fingerprint` (plus HTF, extra-clock, and
   additional-instrument fingerprints), so every run started with `data-catalog` and hand-copied
   fingerprints. Studies also required explicit `evaluation_start` / `evaluation_end`, while
   backtests already defaulted omitted bounds to the common covered window.
2. A cross-market study needed one cloned strategy per market (`markets[].strategy_id`).
3. Sweeps and WFO were capped at 8 candidates, even as async jobs in the research worker.
4. The operator `products` report showed only ids, currencies, and `trading_enabled`, so order
   constraints were invisible before trading.
5. `thytrader-data watch-add --product-id BTC-USDC …` intermittently returned
   `HTTP 400: BTC-USDC is not an enabled USD or USDC spot product.`, and a retry succeeded.
6. Several CLIs collapsed failures into "… failed safely" (research `show-backtest-job` /
   `show-result` under load, operator `data-catalog`, and `plan-study` with an unreadable file).
7. `app.css` declared the skeleton and spinner animations after its own reduced-motion rule, so
   reduced motion never applied app-wide (Home pinned it locally, ADR 0084).
8. Docs said the API is at `127.0.0.1:8200`, but installs override `THYTRADER_API_PORT`.

## Decision

**Omitted datasets bind the newest complete catalog dataset.** On `POST /api/v1/backtests` and
study plan/submit, every omitted dataset fingerprint is bound for each clock the strategy needs:
the decision clock, the HTF filter, each extra indicator clock, and each additional instrument
(with its own HTF and extra clocks). The binding is the newest catalog-verified complete revision
(`DatasetStore.list_latest_verified`, ADR 0085) for that product and timeframe from the configured
ingestion provider (`coinbase` with credentials, else `demo`, matching what `watch-add` / `ingest`
write). Explicit fingerprints are used exactly as sent, and the two may be mixed. The catalog is
listed only when something is omitted. Every response echoes `bound_datasets`
(`product_id`, `timeframe`, `role` `decision|filter|indicator`, `dataset_fingerprint`, `source`
`request|latest_catalog`); the bound fingerprints are the run's inputs, so execution, request, and
plan fingerprints stay exact. A clock with no cataloged dataset fails closed with HTTP 422
`datasets_missing`: `detail.missing[]` lists `{product_id, timeframe, role}` and the message names
the `thytrader-data watch-add … --confirm` and `ingest … --confirm` commands. Internally the exact
`BacktestSubmissionRequest` still requires `dataset_fingerprint`; only the agent start
(`BacktestStartRequest`) makes it optional.

**Studies may omit both bounds.** Omitted `evaluation_start` / `evaluation_end` become the common
covered window: the intersection of every child's default backtest window
(`resolve_backtest_window`, the same rule a backtest with omitted bounds uses), over every
cross-market leg and every sweep/WFO candidate (derived candidates may need more warmup). Sending
only one bound is a 422. Plan, submit, and async 202 bodies echo `evaluation_start`,
`evaluation_end`, and `bound_datasets`. The echo fields sit outside the canonical plan and study
documents and change no fingerprint.

**Cross-market variants of one strategy.** A cross-market start may name one top-level
`strategy_id` plus `markets[].product_id` (2–8 distinct products) instead of `markets[].strategy_id`;
the two forms never mix. The server re-targets the base definition's instrument at each product,
copying rules, sizing, exits, and execution exactly, suffixing the name with the product, and
tagging `research-market-variant`. Like sweep variants (ADR 0082), variants keep the base
`strategy_id`, are recorded as content-addressed snapshots, and are deleted with their strategy.
The base's own product reuses its snapshot. Multi-instrument strategies are refused. Plan and
submit both record variants idempotently, so every child names an exact `strategy_fingerprint`.

**Sync and async study budgets.** Requests may carry up to 64 candidates (`MAX_CANDIDATES`,
`candidate_strategy_ids` or the `parameter_axes` Cartesian product; still ≤ 8 values per axis and
≤ 4 axes). A synchronous submit allows 8 candidates and 128 child windows; an async job allows 64
candidates and 512 child windows. A synchronous submit over budget is 422 `study_budget_exceeded`
naming `--async`. Async submits are planned before they are queued (snapshots, derived candidates,
dataset bounds, window budget), so they fail with the same 422s instead of failing in the worker.
`plan-study` plans against the async budget. Plans above the synchronous budget warn that they run
only as async jobs, and grids above 8 candidates add a data-snooping warning; the existing
"sweep mean is not an out-of-sample claim" warning is unchanged.

**Products report carries order constraints.** Operator `products` rows add `price_increment`,
`base_increment`, `quote_increment`, `base_min_size`, `quote_min_size` (exact decimal strings),
`status`, and `alias`, from the Coinbase product metadata already parsed for order validation.
`MarketProduct` gains optional `status` and `alias`; Coinbase alias twins carry their source's
status and name it as `alias`.

**Watch-add answers an unverifiable catalog with a retryable 503.** Root cause: the enablement
check trusted any successful Coinbase listing and had no third outcome. An empty, paginated, or
short listing (Coinbase reports `num_products` and `pagination.has_next`, which the adapter
ignored) made every product look absent, so the route returned a definitive 400; and a catalog
that failed to load (timeout, 429, 5xx) also returned 400, because the route mapped errors to 503
only when their text contained "unavailable". Now the Coinbase adapter refuses an empty or partial
listing, and `_require_spot_product` raises `ProductCatalogUnavailableError` for any catalog
failure, which the data route maps to HTTP 503 "Could not verify the spot product list … retry the
same command". Only a complete listing can produce the 400, which now points at
`thytrader-operator products`.

**Agent CLIs say what failed.** `agent_http.request_json` maps connection resets, closes before the
status line, and truncated bodies to `AgentHttpError(dropped=True)` ("retry the read" for GET,
"check state before repeating the mutation" otherwise), names the resolved origin and the base-URL
resolution order when the API is unreachable, parses an error body before shortening it, prints
`HTTP <status> <detail.code>: <detail.message>` (FastAPI validation lists become `field: problem`),
and adds a next step to 5xx responses. Every agent CLI (operator, data, research, runtime,
playbook, memory) replaces its generic "failed safely" catch-all with `describe_unexpected_failure`,
which names schema mismatches (field and the `make run` remedy), unreadable files, invalid JSON,
and any other exception type plus its first line, keeps the lane's safety statement, and redacts
configured secrets. Submit-study treats a dropped connection as ambiguous, like a timeout.

**Reduced motion is last in `app.css`.** The rule moves to the end of the file and uses
`animation: none !important` for `.skeleton` and `.spinning`, so it also stills component-scoped
copies; Home's local workaround is removed.

**Docs resolve the base URL from settings.** Skills and user docs say the CLIs resolve
`--base-url`, then `THYTRADER_API_BASE_URL`, then `THYTRADER_API_HOST` / `THYTRADER_API_PORT`
(default `127.0.0.1:8200`), and that raw `curl` should use `"$THYTRADER_API_BASE_URL"`.

**Contract identity.** Ops contract `thytrader-ops-contract-v49` adds
`research_dataset_autobind: ["backtest", "study"]` and
`study_budgets: {sync: {candidates: 8, windows: 128}, async: {candidates: 64, windows: 512}}`.
Alembic stays `0054`; no schema change.

## Consequences

- Agents can run a backtest or study from a strategy id and costs alone once data is ingested, and
  still get exact, recorded dataset identities. A newer dataset revision changes what an omitted
  fingerprint binds to, so comparisons that must hold data fixed should send explicit fingerprints
  (the echo provides them).
- Larger searches are possible but cost more worker time and raise data-snooping risk; the
  warnings and the OOS-only selection rules (ADR 0044) remain the honesty guard.
- Async study submits now do planning work before returning 202 and can return 422/503 directly.
- Tests: `tests/research/test_dataset_binding.py`, `tests/research/test_market_variants.py`,
  `tests/api/test_research_binding.py`, study budget tests in `tests/research/test_studies.py`,
  catalog completeness in `tests/exchanges/test_coinbase_market_data.py`, the watch-add 503 in
  `tests/api/test_data.py`, the products report in `tests/api/test_operator.py`, transport and
  detail parsing in `tests/operator_diagnostics/test_http_client.py`, CLI failure text in
  `tests/test_cli_errors.py`, `tests/research/test_mutation_cli.py` (plan-study 422), and
  `tests/operator_diagnostics/test_cli.py`, and `web/src/lib/reduced-motion.spec.ts`.

## Alternatives considered

- Bind the newest dataset of any provider: rejected; a Coinbase install could silently bind demo
  candles.
- Put `bound_datasets` inside the canonical plan or study document: rejected; it would change plan
  and study fingerprints for unchanged inputs and break catalog dedupe.
- Retry the product listing inside watch-add: rejected as the fix; a retry hides the degraded
  answer instead of classifying it, and still reported 400 when both attempts degraded.
- Raise the synchronous cap: rejected; a synchronous submit runs every child inside one HTTP request.
