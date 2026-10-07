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
- Adapter: `CoinbaseAccount.list_open_orders` plus optional read capability exposed by
  `PortfolioService` (`ExchangeAccount` is unchanged). Spot-history pages retain all
  recognized nonterminal statuses; malformed evidence fails closed.
  `ExchangeReadOperation.OPEN_ORDERS`.
- UI: `web/src/lib/PreflightPanel.svelte` on Home and Portfolio (`/deployments`).

Neither report places, cancels, or tightens policy. `payload.account.enforcement`
is `advisory_only`. Account math is cost basis plus entry remainders, not live marks.
Comparisons depending on an incomplete managed OR venue listing are null.

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
- Coinbase listing pages all spot history (no status/time/source/account filters),
  retaining OPEN/PENDING/QUEUED/CANCEL_QUEUED/EDIT_QUEUED after strict validation.
  Queued cancellation is not confirmed cancellation. Page-bound exhaustion makes
  the listing unavailable, not partial/complete. Sequential reads are not atomic.
- Venue order rows are capped at 50 foreign rows (`foreign_truncated`).
- Portfolio sections use configured capital and stored limits; they do not simulate
  a new entry. Deployment filters narrow displayed rows, NOT sibling cap accounting.
- UI panel is fleet-scoped readiness, not a per-portfolio filter control.
- Root GitNexus was locked by the lead rebuild; impact for shared symbols used the
  earlier root read plus source confirmation of `REPORT_KINDS` callers.

## Initial resume-session verification (2026-10-06 afternoon; before lead review)

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

## Lead-review follow-up (all six findings)

1. Account capital, inventory cost, entry reservations, exposure and product caps now
   follow actual product quote. Other-quote products are `excluded_products`, never
   converted or assigned the policy quote cap. Mixed deployments expose
   `quote_exposures[]`; their cross-quote totals/allocation remaining are null. The
   metric is explicitly cost basis plus entry remainders, not live-marked exposure.
2. `ReadinessInventoryEvidence` records status/read/expected counts, missing deployment
   IDs, unsupported products, and unpriced entry IDs. Account and portfolio dependent
   totals/capacities are null if the required managed scope is incomplete. All
   snapshots are read once; deployment rows stay scoped, while account/portfolio
   sections use full required inventory. Paper starting cash uses listed deployment
   configuration so missing snapshots cannot shrink the commitment.
   Venue `managed_listing` records every live-book read (including stopped books and
   historical order claims); incomplete managed reads null quantity totals and
   foreign/orphan/matched claims even when venue reads succeed. `managed_unknown`
   rows retain observed venue quantities, without classifying ownership.
3. Venue evidence defaults are unavailable, so `_failed_report` constructs valid
   envelopes. None/disabled execution stores and list failures produce failed reports,
   not healthy empty fleets; empty in-memory stores remain a known empty fleet.
4. Sibling exposures consume portfolio capacity even in a single-deployment preflight.
   `account_breaker_comparable` is false for paper/other-quote portfolios; daily-stop
   comparisons are `not_comparable` rather than equivalent scopes. Unknown account
   allowance or mixed/unreadable members make the comparison unknown.
5. Runtime failure yields `runtime_available=false`, `breaker_latched=null` and no
   fabricated drawdown allowance. `portfolio_scope_complete=false` and a material
   unknown finding grade read failures/missing storage/the 100-row portfolio bound
   as degraded. UI never shows Clear for failed/degraded envelopes with no findings;
   refresh failure clears stale evidence, and null capacity remains an em dash.
6. Coinbase validates orders/has_next and every row; identity/product/side/status
   gaps, unknown statuses, duplicate IDs, cursor cycles/missing cursors, later-page
   failures and page exhaustion fail the whole listing. No status filter hides
   queued cancellation/edit or pending orders. Recognized terminal statuses are
   filtered only after validation. Malformed balance rows/flags also fail the whole
   listing, rather than proving an empty venue. Pending managed submits match by
   client ID. Venue working orders claimed by local terminal/stopped records are
   `MANAGED_ORDER_STATUS_MISMATCH`, not external orders. No cancel/replace/entry-gate
   changes, no production resources, credentials, services, or DB access.

### Interfaces for lead integration

- Routes/CLI unchanged. Typed public shapes live only in this slice's two modules.
- Added account `inventory`, `excluded_products`, `exposure_basis`; current exposure,
  inventory cost and buy reservations are nullable, as are dependent cap/capacity.
- Deployment totals are nullable, with per-quote rows and unknown-notional IDs.
- Added portfolio inventory/exclusions/runtime availability/breaker comparability;
  latch and exposure/capacity totals are nullable; `not_comparable` is a new daily
  comparator value. Payload adds scoped inventory and portfolio-scope completeness.
- Venue adds `managed_listing`, nullable managed book/order/quantity totals,
  `managed_unknown` asset classification, listing scope, optional order client ID
  for ownership matching, and `MANAGED_ORDER_STATUS_MISMATCH` finding.
- No shared JSON Schema, release contract, OPS_CONTRACT_ID, schema revision, ADR index,
  or version edits in this follow-up. Lead regenerates/integrates those against these
  models. The initial commit's shared enum additions remain untouched.

### Follow-up verification

- `uv run pytest tests/operator_diagnostics/ tests/exchanges/ tests/portfolio/
  tests/api/test_operator_readiness.py tests/api/test_portfolio.py tests/api/test_fees.py -q`:
  **343 passed** in 25.70s; one pre-existing Alembic configuration DeprecationWarning.
  No production/test PostgreSQL URLs, no live/network calls.
- `uv run ruff check .`: pass. `uv run ruff format --check .`: pass (869 files).
  `uv run ty check`: pass, zero diagnostics and no new suppressions.
- Web: `npx vitest run src/lib/preflight.spec.ts`: **6 passed**;
  `npm run check`: 0 errors / 0 warnings; `npm run lint`: pass;
  `npm run build`: pass (Vite client/server builds 2.39s / 7.79s).
- Final recheck of the six regression files/API readiness: **65 passed** in 4.28s
  after the credential-visibility docstring clarification; Ruff/format/ty pass again.
- `git diff --check`: pass.
- Existing local GitNexus impact: readiness/portfolio/report callers LOW; dynamic
  Coinbase read method UNKNOWN, confirmed via source/optional capability caller.
  Local `detect_changes(scope=all, limit=1000)` final raw result: **14 files / 144 symbols /
  15 processes, HIGH risk**; all 144 symbols and all 15 flows returned, no partial or
  truncated flags. CLI display abbreviation is not used as evidence of completeness.
  Outputs: `/tmp/tt-readiness-review-detect.json` and
  `/tmp/tt-readiness-review-verification.log`.

The initial full-suite result above is historical, not a claim that this follow-up
ran it. Per lead resource guidance no full graph rebuild or full Python suite was
launched here. Impact uses the existing worktree graph (metadata one commit behind
704c799; source confirms dynamic callers). This is not a claim of a fresh integrated
graph. Lead owns the serialized integrated graph/embedding/PDG rebuild, global JSON
schema regeneration, release/version contracts, and complete combined suite.
