# Slice completion: readiness preflight and venue reconciliation (ADR 0114)

Worktree: `/home/hermes/projects/tt-review-readiness` on `feat/review-readiness`.
Read-only. No live mutations, no `.env` reads, no production database, no policy edits.

## Interfaces

- `GET /api/v1/operator/readiness?deployment_id=&portfolio_id=`
- `GET /api/v1/operator/venue-reconciliation`
- CLI: `thytrader-operator readiness [--deployment-id UUID] [--portfolio-id UUID]`
- CLI: `thytrader-operator venue-reconciliation`
- Report kinds: `readiness`, `venue_reconciliation` (`thytrader-operator-report-v1`)
- Models: `thytrader.operator.readiness`, `thytrader.operator.venue_reconciliation`
- Adapter: `ExchangeAccount.list_open_orders` / `CoinbaseAccount.list_open_orders`
  (OPEN spot pages, fail-closed cursor). `ExchangeReadOperation.OPEN_ORDERS`.
- UI: `web/src/lib/PreflightPanel.svelte` on Home and Portfolio (`/deployments`).

Neither report places, cancels, or tightens policy. `payload.account.enforcement`
is `advisory_only`. Venue comparisons that depend on an incomplete listing are null.

## Finding codes

Readiness: `ALLOCATION_OVERCOMMITMENT` (advisory), `ACCOUNT_EXPOSURE_CAP_EXCEEDED`,
`PRODUCT_EXPOSURE_CAP_EXCEEDED`, `PORTFOLIO_EXPOSURE_CAP_EXCEEDED`,
`PORTFOLIO_ASSET_EXPOSURE_CAP_EXCEEDED` (violations), `VENUE_BALANCE_UNKNOWN`,
`FEE_EVIDENCE_UNAVAILABLE`, `PAPER_FEE_ASSUMPTION_MORE_OPTIMISTIC`,
`QUOTE_CURRENCY_MISMATCH`, `PORTFOLIO_BREAKER_LATCHED`, `BREAKER_LATCHED`.

Venue: `EXTERNAL_INVENTORY` and `EXTERNAL_OPEN_ORDERS` (info, not flattened),
`MANAGED_INVENTORY_SHORTFALL`, `MANAGED_ORDER_NOT_AT_VENUE` (warnings),
`VENUE_BALANCES_LISTING_INCOMPLETE`, `VENUE_ORDERS_LISTING_INCOMPLETE` (unknown),
`DUPLICATE_BALANCE_ROWS` (info; rows are summed).

## Tests

Hermetic only. See the command log in the commit message / agent return for exact
results. Covered: 8x40 vs 80 cap advisory, actual exposure violation, quote-currency
separation, duplicate quote rows, absent venue balance, paper 0.001/0.002 vs account
0.005/0.009, absent fee evidence, demo fee evidence not compared, tighter portfolio
daily stop, foreign holdings, managed shortfall, duplicate asset rows, incomplete
listings, orphan vs foreign orders, paginated/repeated/missing open-order cursors,
HTTP GET-only envelopes.

## Limitations for lead

- Ops contract version, architecture index, roadmap, and global schema merge are
  not bumped here.
- Readiness account math uses the policy quote currency only. Books in another
  quote are disclosed, not converted.
- Open-order listing is Coinbase status `OPEN` only. `CANCEL_QUEUED` is not treated
  as resting; a managed order in that venue state can look orphaned until the next
  local status update.
- Venue order rows are capped at 50 foreign rows (`foreign_truncated`).
- Portfolio sections use configured capital and stored limits; they do not simulate
  a new entry.
- UI panel is fleet-scoped readiness, not a per-portfolio filter control.
- Root GitNexus was locked by the lead rebuild; impact for shared symbols used the
  earlier root read plus source confirmation of `REPORT_KINDS` callers.

## Resume-session corrections (2026-10-06 afternoon)

- `ExchangeAccount` stays exactly as on HEAD. Adding `list_open_orders` to the
  shared protocol made 9 duck-typed test fakes in untouched files fail `ty check`.
  The capability now ships as concrete methods on `CoinbaseAccount` and
  `DemoExchangeAccount` plus a capability probe in `PortfolioService.list_open_orders`
  that fails closed with a typed `ExchangeReadError(OPEN_ORDERS, UNSUPPORTED)` so an
  adapter without the listing degrades to an unknown listing, never an empty book.
  `ExchangeReadFailureKind.UNSUPPORTED` is a new enum member (schema enum appended).
- `readiness.py` defines a narrow `ReadinessPortfolioDirectory` protocol
  (`list_page` / `get` / `runtime_state`) instead of requiring full `PortfolioStorage`,
  so hermetic test stubs satisfy it structurally.
- The venue-reconciliation redaction test now asserts absence of `account_id` /
  `api_key` object keys via parsed-JSON key walk; the earlier substring assertion
  false-tripped on the pre-existing envelope flag `account_identifiers_omitted`.
- Verification (this worktree venv): `uv run pytest` 2783 passed / 89 skipped
  (PostgreSQL integration skipped; hermetic), `ruff check .` clean,
  `ruff format --check .` clean, `ty check` 0 diagnostics (HEAD baseline verified 0
  via detached worktree). Web: `vitest run src/lib/preflight.spec.ts` 3 passed,
  `svelte-check` 0 errors/0 warnings, `prettier --check` + `eslint` clean,
  `vite build` succeeds.
- GitNexus: the copied worktree index was foreign (metadata pointed at the main
  checkout); it was moved aside and a fresh local graph was indexed as
  `tt-review-readiness` (83,839 nodes / 189,220 edges; embeddings skipped by the
  50,000-node safety cap — not overridden). `detect-changes --scope all` reports
  22 files / 53 symbols, risk **high** — expected: the slice's assigned surfaces are
  shared (operator routes/service, Coinbase adapter, skill text). No unrelated files
  changed; shared-file edits are additive (report kinds, enum members, schema enums,
  one CLI subcommand pair, one route pair).
