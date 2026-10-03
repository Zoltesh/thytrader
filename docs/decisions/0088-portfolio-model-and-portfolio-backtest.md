# 0088: Portfolio model and portfolio backtest (foundation, no deployment)

- Status: Accepted (extended by [0091](0091-portfolio-deployment-limits-and-manager-proposals.md):
  portfolio deployment, limits binding orders, and the manager proposals ship there; and
  [0101](0101-atomic-portfolio-creation-with-sleeves.md): optional initial sleeves at revision 1)
- Date: 2026-10-02
- Relates to: [0082](0082-strategy-root-mutable-strategies-auto-snapshots.md) (strategies are
  the root object), [0083](0083-unified-backtest-model.md) (one backtest model),
  [0033](0033-phase-10-risk-policy-registry.md) (risk-policy registry),
  [0069](0069-async-backtest-jobs-study-summary.md) / [0073](0073-durable-research-jobs.md)
  (async research jobs), [0077](0077-derived-performance-metrics.md) (derived metrics),
  [0011](0011-derived-buy-and-hold-benchmark.md) (buy-and-hold benchmark),
  [0081](0081-live-chrome-portfolio-bot-detail-trade.md) (Portfolio page)

## Context

Each bot runs one strategy on its own capital. The operator's next unlock is portfolio-level
trading: several strategies sharing one pot of capital under shared limits, optionally run by a
manager agent that moves capital and pauses sleeves while the strategies place every trade. Before
anything trades as a portfolio, operators and agents need to compose portfolios, keep an honest
history of every change, and see how the sleeves would have done together.

## Decision

**Portfolio.** A portfolio (`portfolios`, `portfolio_id` UUIDv7) has a `name`, a `mode`
(`paper` or `live`, never mixed), a `quote_currency` (USD, USDC, or USDT), `capital_quote`, a
`cash_reserve_fraction` that is never allocated, a `revision` counter, and timestamps. Mode and
quote currency are fixed at creation (an update naming them is refused with a clear message);
make a new portfolio instead. Money is canonical decimal text with at most eight places;
fractions have at most four (0.01%).

**Limits** live on the portfolio: `max_total_exposure_fraction`, `max_per_asset_fraction`,
optional `daily_loss_quote`, optional `max_drawdown_fraction`. They are stored and shown now and
**start binding orders only when portfolio deployment ships**; today no order passes through them.

**Manager settings**: a `mandate` (plain text, at most 2,000 characters) and permissions —
`may_rebalance` with `max_weight_change_per_week`, `may_pause_sleeves`, `may_propose_sleeves`.
There is no "may place orders" permission and none can be added: any other `may_*` key is
refused with "the manager never places orders; strategies place every trade". No manager loop
acts on these settings in this ADR.

**Sleeves** (`portfolio_sleeves`): one strategy per sleeve (`strategy_id` foreign key) with a
`weight_fraction` and an optional note. Rules, checked with exact decimals in the same
transaction that writes the change:

- sleeve weights plus the cash reserve never exceed 1;
- at most one sleeve per strategy per portfolio;
- the strategy must be quoted in the portfolio's quote currency (multi-product strategies share
  one quote currency, so the primary product decides);
- at most 20 sleeves.

Strategies stay mutable, so a sleeve can drift after it is added; reads flag it with sleeve
issues (`strategy_invalid`, `quote_currency_mismatch`, `product_unknown`) and backtests refuse it.
Allocation reads report allocated, reserve, and unallocated capital and the **largest single
asset** (a multi-product sleeve counts its full weight toward each covered asset) against
`max_per_asset_fraction`.

**Deleting a strategy removes its sleeves.** Inside the strategy-deletion transaction, after the
strategy row is locked and before it is deleted, every sleeve holding it is removed with a
`sleeve_removed` journal entry (actor `system`, reason `strategy_deleted`) and its portfolio's
revision advances. The foreign key cascades as a safety net. Deletion previews and results count
`portfolio_sleeves`. Lock order is always strategy row first, then portfolio rows in
`portfolio_id` order, so sleeve additions and strategy deletions cannot deadlock.

**Journal** (`portfolio_journal_entries`): append-only events `created`, `settings_changed`,
`sleeve_added`, `sleeve_updated`, `sleeve_removed`, `weights_changed`, `limits_changed`,
`manager_changed`, and `backtest_run`, each with `actor` (`operator`, `system`, or `manager` —
reserved for the manager agent), `channel` (`browser` when the request carried an Origin, else
`api`; `system` for automatic consequences), a one-sentence `summary`, structured `detail`
(before/after values), and the portfolio `revision` it produced. Manager proposals will add
kinds without changing the shape.

**Revision guards.** Every mutation names the revision it was planned against; a stale one is
HTTP 409 `portfolio_revision_conflict` with `current_revision`, never an overwrite or merge.

**Portfolio backtest** (async, like research jobs):

1. *Plan at submit time* (so rejections are an immediate 422 `portfolio_backtest_rejected` with
   one problem per sleeve, and the queued job is reproducible from its payload): snapshot every
   sleeve's strategy (it must currently validate); bind request-supplied dataset fingerprints, or
   the latest verified complete Coinbase dataset for every clock the strategy declares (decision,
   HTF filter, extra indicator clocks, additional instruments with their own HTF/extra clocks);
   resolve each sleeve's usable window exactly as a single backtest would; intersect them and
   align the start up and the end down to the least common multiple of the sleeves' bar
   durations, so every sleeve evaluates the same bars. A supplied window is verified for every
   sleeve instead.
2. *Run* each sleeve through the unified model (ADR 0083) with `capital = weight × capital_quote`
   and the shared maker/taker/slippage/spread assumptions. Child runs and results are ordinary
   published backtests (deduplicated by execution fingerprint).
3. *Combine* (contract `thytrader-portfolio-backtest-v1`). A child equity point stamped at a bar
   start marks that bar's close, so it is placed at `starts_at + bar`; the terminal liquidation
   point replaces the last close at `evaluation_end`. The grid is the window start plus the union
   of every sleeve's mark instants; each sleeve is forward-filled (its capital before its first
   mark); combined equity adds the reserve and unallocated cash. Reported: the combined curve,
   total return, max drawdown, ratio metrics through the performance-metrics formulas (rf 0,
   annualized on the grid's median spacing), each sleeve's contribution (sleeve PnL / portfolio
   capital, so contributions sum to the total return) and standalone return, pairwise Pearson
   correlations of sleeve returns and each sleeve's correlation to the rest, sampled every `lcm`
   of the bar durations (where every sleeve has a fresh mark), time-weighted overlap (share of the
   window with at least two sleeves long the same asset, and any two long together), time-weighted
   idle capital, and the buy-and-hold of an equal-weight basket of the sleeves' primary products
   (taker entry at the first open, stressed-bid marks, taker exit at the window-end open; the
   reserve stays cash). Arithmetic runs under the simulation's Decimal64 context.
4. *Persist* the canonical result (`published_portfolio_backtests`, `sha256` of sorted compact
   JSON) referencing every child run and result fingerprint, re-verified on every load, and journal
   `backtest_run`.

**Disclosure, on every result:** "Sleeves simulated independently on fixed capital slices;
portfolio-level caps and cross-sleeve interactions are not simulated." Multi-product sleeves are
excluded from overlap because their results do not attribute positions to one product, and the
result says so.

**Interfaces.** `/api/v1/portfolios` (list, create, get, PATCH settings/limits/manager, DELETE
with `?revision=`, sleeves POST/PATCH/DELETE, `PUT /weights`, journal, backtests submit/list,
jobs list/poll, result with optional `max_points` display thinning) behind the trust boundary
(installation auth; CSRF from a browser). A confirmation-gated `thytrader-portfolio` CLI (list,
show, create, update, add-sleeve, remove-sleeve, set-weights, backtest, show-backtest,
list-backtests, journal; `--confirm` on every mutation and YOLO never skips it). Operator report
kind `portfolios`. Ops contract `thytrader-ops-contract-v48` adds `portfolio_model`,
`portfolio_modes`, `portfolio_backtest_contract`, and `max_concurrent_portfolio_backtests`
(1), with expected Alembic revision `0054`. The Portfolio page (`/deployments`) gains the
portfolio switcher, a New portfolio dialog, and Sleeves, Portfolio backtest, Manager, and Limits
tabs above the unchanged All bots list. Nothing here deploys a portfolio: the page shows
"Deploy portfolio" disabled and every response carries `deployable: false`.

## Consequences

- Operators and agents can compose portfolios, keep a truthful change history, and compare the
  sleeves together against an equal-weight buy-and-hold before anything trades as a portfolio.
- Portfolio backtests are optimistic about portfolio caps (none bind) and blind to cross-sleeve
  netting and shared cash; the disclosure says so. Comparing to a single sleeve uses the same
  model and costs, so the relative picture is honest.
- Deleting a strategy changes portfolios it belonged to; the deletion preview counts the sleeves
  and each portfolio's journal records why. Stored portfolio backtests are kept as history even
  when a child result is later deleted with its strategy.
- Follow-up (not in this ADR): portfolio deployment and risk-gate integration (limits binding
  orders, shared cash), the manager agent loop with approve/decline proposals, and job
  cancellation.

## Alternatives considered

- **Put portfolio jobs in `research_jobs`.** Rejected: research jobs belong to exactly one
  strategy (foreign key), and a portfolio backtest spans many.
- **Simulate the sleeves jointly with shared cash and caps now.** Rejected for the foundation: it
  needs the cross-sleeve order arbitration the deployment slice will define; simulating it twice
  risks backtests and live disagreeing. Independent sleeves are disclosed instead.
- **Forward-fill on a regular grid at the finest clock.** Rejected in favor of the union of mark
  instants (no invented points); correlations use the coarsest common clock so forward-filled
  zero returns never enter them.
- **Allow mode or quote changes on a portfolio.** Rejected: a live portfolio turning paper (or a
  quote switch) would silently change what its history means; create a new portfolio instead.
- **Delete portfolio backtests that include a deleted strategy.** Rejected: the combined result is
  self-contained portfolio history; its child references may dangle and that is disclosed.
