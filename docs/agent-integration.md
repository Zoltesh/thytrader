# Agent and Operator Integration

[ADR 0121](decisions/0121-execution-write-boundaries.md) fences reused discretionary pending
state, commits scoped runtime/parent updates together, serializes same-book fill projection and
restricts peer breaker pauses to metadata. A conflicting entry requires fresh observation and
admission, not retrying stale financial state under a new revision. Protection replacement waits
for applied post-cancel execution evidence; uncertainty cannot supply a sell quantity. No new CLI
flags, confirmation bypass, public payload or migration is introduced by this correction.
The report's missing occupied-product inventory predicate also constrains admission without a
price observation, verified UTC opening reconstruction and flat-day accounting. Successful full
reads and prior opening proof do not turn a missing OPEN/PENDING_EXIT position into zero risk.

Runtime risk scope and diagnostic semantics follow [ADR 0106](decisions/0106-account-risk-capital-and-live-startup-baselines.md).
Live account fractions use one observed venue quote balance plus managed long inventory cost and
buy-entry reservations; per-bot allocations and portfolio caps remain separate. Existing decision
`risk.detail` strings expose exact exposure/capital/cap values and identify missing inventory marks
versus equity/day-open baselines. Protective bracket/TP maintenance with inventory is `holding`,
with orders linked, rather than a canceled entry. Historical rows are not rewritten. No API shape,
CLI invocation, confirmation, or live-arming change is required by ADR 0106.

Operator HTTP reports validate the complete JSON contract, including populated decision
timelines with strict nested `rule.signal.candle_starts_at` timestamps. Valid UTC strings are
accepted at the JSON boundary; malformed, naive, or non-UTC signal timestamps are rejected.
A report-validation error is an observation failure, never evidence of an empty timeline.

Operator `strategies` bounds library rows to the 100 most recently updated strategies and reports
truncation in `partial_result_warnings`. Missing rows do not prove deletion or rule mismatch.
Read an older deployment's current rules with `thytrader-research show-strategy --strategy-id UUID`,
or page the complete library with `list-strategies --limit 100` and its returned `--cursor`.

[ADR 0107](decisions/0107-capital-normalized-live-performance.md) adds pinned performance capital
and durable observed maximum drawdown. Health advertises ops contract v64 / Alembic `0061` and
`runtime_observability: capital_normalized_performance`. Runtime `show UUID` exposes
`capital.performance_capital_quote` and `capital.performance_maximum_drawdown_fraction`.
Operator `performance --deployment-id UUID` and portfolio sleeve metrics use that pinned budget;
rebalances do not reset it. Ledger cash and dollar PnL are unchanged. Missing capital or marks
leave percentage metrics unknown and block new entries. Maximum drawdown covers recorded fill
and worker observations, not an invented historical candle curve. The breaker uses current
drawdown; reset clears its latch without erasing the peak/history or resuming the bot.

## Recommendation

ThyTrader should ship repository-level agent skills as supported UI/API contracts become available.
The first skill remains read-only and requires stable, versioned operational interfaces. A skill is
documentation and workflow; it must not compensate for a missing product API by scraping logs,
querying PostgreSQL directly, or importing private internals.

A `skills/` directory is reserved now so agent integration evolves as the **primary product surface**
([ADR 0030](decisions/0030-agent-e2e-primary-surface.md)) rather than an afterthought. A modern
professional UI remains required; it is not a substitute for these contracts.

## Operating models and primacy

ThyTrader supports three operating models. All remain fully supported; none is deprecated. Agent-driven
E2E is the **primary design target** ([ADR 0030](decisions/0030-agent-e2e-primary-surface.md)):

1. **100% human-driven** — every observation and mutation through the browser and CLIs, subject to the same confirmation gates.
2. **100% agent-driven** — diagnosis, data ingest, research, journals, notifications, and paper/live control end-to-end through the shipped skills, within explicitly granted, confirmation-gated authority (`--confirm`; live additionally `--i-understand-live`).
3. **Collaborative human + agent** — a human and an agent share the operating loop.

Safety rests on confirmation gating, scoped authority, immutable evidence, auditability, and risk
controls—not on excluding agents. No model requires an agent; no model excludes one. A capability is
incomplete until the agent contract exists.

Production loopback installs also enforce an application trust boundary
([ADR 0061](decisions/0061-application-trust-boundary.md)): HTTP mutations require an installation
credential and browser writes require CSRF. Live arming remains the published-risk-policy gate from
[ADR 0063](decisions/0063-stage-5-release-discipline-ci-risk-defaults-rate-budget.md) plus CLI
`--i-understand-live`. Every mutation lane CLI (`thytrader-data`, `thytrader-research`,
`thytrader-memory`, `thytrader-runtime`, and YOLO skip audits) sends installation Bearer auth via
the shared `request_mutation_json()` helper ([ADR 0070](decisions/0070-mutation-cli-installation-auth.md)).
Agent CLIs read the token from `THYTRADER_INSTALLATION_TOKEN` or the shared credentials directory.
The browser bootstraps `GET /api/v1/security/session` before strategy, backtest, study, chat, and
credential mutations, then sends the matching `X-CSRF-Token` and cookie through the web proxy.
Read-only browser requests do not need a CSRF session; CLI writes use installation auth without
browser CSRF. Tests must exercise a trust-boundary-enabled browser POST rather than only mocked
mutation responses.

## Planned direction: agent experts that learn from evidence (hooks + V1 trainer + why-trade)

A major product goal is for agents to act as **crypto-trading experts that improve from durable
evidence** spanning market-data research, reproducible backtests, paper trades, and live trades.
Phase 14 shipped origin-attributed **hooks** ([ADR 0037](decisions/0037-phase-14-experiential-memory.md)).
Bounded V1 training ships as a fail-closed integer ranker over those attributed local journals
([ADR 0049](decisions/0049-experiential-train-v1.md)). Per-intent why-trade review ships as
`thytrader-trade-reason-v1` ([ADR 0054](decisions/0054-trade-reason-journals.md)):

- Durable journals, sentiment snapshots, and pattern observations with required `origin` (`human` or
  `agent`). Per-trade **why it was made** records freeze the strategy snapshot identity, closed-bar
  signal, risk verdict, and notes at persist; fill/reconcile facts join from the ledger on read.
- Read-only monitor of deployments, recent journals, why-trade records, and notification delivery.
- Config-gated user notification (`none` default, `log`, or `webhook`).
- `thytrader-memory train` (and `GET|POST /api/v1/memory/models`) never bypasses confirmation gates,
  scoped authority, auditability, or risk controls, and never substitutes for audit trails. YOLO
  never covers memory mutations. Output is advisory research input, not a live brain. The trainer
  consumes `JournalEntry` as stored.

See [roadmap Phase 14](roadmap.md#phase-14-experiential-memory--hindsight--shipped),
[bounded experiential training V1](roadmap.md#bounded-experiential-training-v1--shipped),
[trade-reason journals](roadmap.md#trade-reason-journals--shipped), and
[multi-instrument documents and pyramiding](roadmap.md#multi-instrument-documents-and-intra-strategy-pyramiding--shipped).

## Initial use cases

A user's agent should be able to answer questions such as:

- Is the API, worker, database, and exchange connection healthy?
- Is market data current, complete, and free of known gaps?
- Are configured credentials present and usable, and what permissions were detected, without revealing them?
- Which strategies are running, paused, degraded, or blocked by risk controls?
- How has a strategy performed over a selected period?
- How much did fees, spread, and slippage contribute?
- Are live and backtest assumptions materially different?
- Did restarts, disconnects, rejected orders, or reconciliation anomalies occur?
- Which configuration values are invalid, risky, deprecated, or ineffective?

That research skill is **shipped** as `thytrader-research` (`--confirm` on mutations). It is not an
extension of the read-only operator skill and has no paper, live, arming, cancellation, or
kill-switch authority. Data ingest, paper/live control (including on-demand `place-order`),
playbook sequencing, and memory are the other shipped lane skills listed below.

## Supported interface design

Prefer a versioned `thytrader` operator CLI backed by the same application services as a read-only HTTP API. The CLI talks to that API on loopback by default.

Shipped command groups:

- `thytrader-operator` — health, configuration, exchange, market-data, data-catalog, data-health (all enabled watched tails; clock-aware, historical coverage separate), products, indicators, strategies, performance, risk, readiness, reconciliation, venue-reconciliation, runtime, alerts, execution-quality, monitor, studies, trade-reasons, decisions, portfolios (including `paper_live_fill_comparisons` for paper/live twins of one strategy snapshot; [ADR 0097](decisions/0097-runtime-parity-and-observability.md)), support-bundle, schema-check, chat-status.

`uv run thytrader-operator indicators` lists the fail-closed catalog an agent may author: 53 kinds
grouped by `category` (trend, momentum, volatility, volume, statistical, price), each with its
`inputs`, every parameter's bounds, builder `default`, and one-line `help`, output `series`, the
`warmup` formula, and `default_warmup_bars`
([ADR 0047](decisions/0047-wider-fail-closed-indicator-catalog.md),
[ADR 0086](decisions/0086-indicator-catalog-expansion-and-offset.md)). Any declaration except
`constant` may add `offset` (0–500 completed bars of its own clock) to read an earlier bar, for
example the previous bar's Donchian channel for a breakout. Indicator operands independently
accept strict integer `offset` 0–500, for example `{"indicator":"bands","series":"upper","offset":1}`.
Use one declaration for current/prior reads to keep parameter sweeps synchronized. Lags count
completed native-clock bars before alignment and add to declaration offsets. Warmup includes
the maximum operand lag per indicator across entry, signal-exit, and filter rules. Literals reject
offsets; constants reject positive offsets; omitted/zero preserve canonical bytes. Decision and
research traces add `id@N` / `id.series@N` for positive operand lags
([ADR 0099](decisions/0099-operand-level-indicator-offsets.md)). Do not invent unlisted kinds.
Strategy `summary` text exposes each entry and signal-exit read's combined declaration and
operand lag as `(N bars ago)`; unlagged reads omit the suffix. Reference operands retain their
native clock, for example `BTC · SMA(2) (3 bars ago) [1d]`. This is display text; snapshot
fingerprints and evaluated rules remain unchanged.
- `thytrader-data` — watchlist, ingest, inspect-gaps, fill-gaps (`--confirm` on mutations; `watch-add`, `ingest`, and `fill-gaps` POSTs send installation Bearer when a token is resolvable). `ingest` and `fill-gaps` only queue work for an existing watch: an unwatched product/timeframe is HTTP 409 naming `watch-add`, and no watch is created. `watch-add --lookback-hours` ceilings run from 2160 (90 days) at `1m` to 87600 (10 years) at `2h`-`1d` ([ADR 0085](decisions/0085-fast-research-ingest.md)).
- `thytrader-research` — strategy list/show/create/save/import/clone/delete/bulk-delete and snapshot reads, backtests and composed studies by `strategy_id`, and persisted study catalog reads (`--confirm` on mutations). Multi-instrument documents bind extra products through `additional_instrument_datasets` on submit-backtest JSON (lexicographic `product_id`; omitted when empty) ([ADR 0056](decisions/0056-multi-instrument-documents-and-pyramiding.md)). Omitting both `evaluation_start` and `evaluation_end` on `submit-backtest` fills the common LTF+HTF (and extra-clock) covered intersection. `show-result` reports the snapshot's strategy clock, including `2h` and `4h`.
- `thytrader-runtime` — paper/live start, pause, resume, stop, on-demand place-order, and write-only Coinbase credential show/set/clear (`--confirm` unless YOLO covers that tier; live also `--i-understand-live`; `--side` long or short). Default stop is managed shutdown (keep protective brackets and residual occupancy); `--flatten` / `?flatten=true` marketably exits ([ADR 0058](decisions/0058-protection-lifecycle-accounting.md)). Paper start/place-order may pass `--maker-fee-rate` / `--taker-fee-rate` (documented assumptions; omitted paper uses `0.001` / `0.002`; live rejects the flags). Credential set/clear always need `--confirm` and `--private-key-file` (never a CLI secret). YOLO never covers credentials. Setting credentials does not arm live trading. `set-risk-policy --allow-intra-strategy-pyramiding` is required for paper/live same-side adds when the strategy also enables pyramiding ([ADR 0056](decisions/0056-multi-instrument-documents-and-pyramiding.md)). Live start and live `place-order` require a published risk policy; the compiled default cannot arm live (`LIVE_REQUIRES_PUBLISHED_POLICY`). Optional `--max-daily-loss-quote` / `--max-portfolio-exposure-quote` add absolute quote ceilings alongside the matching fraction, and optional `--max-venue-order-actions-per-minute` adds a combined entry+cancel budget that can only deny a new entry, never a cancellation or protective submission ([ADR 0063](decisions/0063-stage-5-release-discipline-ci-risk-defaults-rate-budget.md)). `list` / `show` return every product book (`positions`, `instrument_runtimes`, product-tagged orders/fills, `book_totals`). The singular `position` field is compatibility-only and always includes `product_id`; read `positions` for inventory ([ADR 0060](decisions/0060-multi-book-deployment-api.md)). `protection_status` is classified from verified attached-child coverage and venue-visible exits (`flat` / `covered` / `unprotected` / `unknown`); missing children are unprotected, not unknown ([ADR 0058](decisions/0058-protection-lifecycle-accounting.md)). Read `position_state` (`flat` / `entering` / `open_protected` / `open_unprotected` / `open_unverified` / `exiting`) and `exit_in_flight` on the deployment and each `positions[]` row rather than the raw `phase`: `phase: pending_exit` includes an open book whose TP/SL merely rests (`open_protected`); only `exiting` means an exit is being sent ([ADR 0097](decisions/0097-runtime-parity-and-observability.md)). Paper resolves a same-bar exit tie exactly like the backtest: stop, then touched TP, then signal exit, then time exit. Default `stop` is managed shutdown; pass `--flatten` or `?flatten=true` only to marketably exit then cancel remainders. Pause still maintains protection and does not reset breaker baselines. Operator `runtime` reports `lifecycle_command`, latches, `revision`, and `worker_lease_held`; `thytrader-runtime show` reports a `capital` block (`allocated_capital`, `venue_available_quote`, reserved/inventory/equity fields) separately from ledger `cash` ([ADR 0065](decisions/0065-deployment-capital-accounting-http.md)).
- Per-bar decision timeline (read-only; [ADR 0087](decisions/0087-per-bar-decision-timeline.md)): every paper and live strategy bot journals one `thytrader-bar-decision-v1` record per completed bar and covered product — `outcome` (`entry_signal`, `no_signal`, `holding`, `exit`, `entry_blocked`, `skipped`, `error`), a one-line `summary` (for example `No trade: RSI(14) 47.21 needs ≥ 50`), the evaluated rule tree with leaf values versus thresholds and the HTF filter, the risk verdict, `action` plus `intent_id`/orders/fills, skip/exit reasons, close price, and the end-of-bar position. Read it with `thytrader-runtime decisions UUID` / `GET /api/v1/deployments/{deployment_id}/decisions`, across a strategy's bots with `thytrader-runtime decisions --strategy-id UUID` / `GET /api/v1/strategies/{strategy_id}/decisions?deployment_id=`, or as operator report kind `decisions` (`thytrader-operator decisions`, `GET /api/v1/operator/decisions`). All accept repeated `outcome` filters and `cursor`/`next_cursor` paging, newest bar first. The in-app chat tool is `runtime_decisions`. Journaling never blocks or changes trading (failed writes are audited as `decision_journal_write_failed`); the journal keeps the newest 20,000 decisions per bot (one per bar and covered product) for at most 180 days.
- `thytrader-portfolio` — portfolio list/show/create/update/delete, add-sleeve/add-sleeves/remove-sleeve/set-weights, portfolio backtests (backtest/show-backtest/list-backtests), the journal, and the manager loop: read-only `deployment` and `briefing` (`thytrader-portfolio-briefing-v1`), `propose` (rebalance, pause_sleeve, resume_sleeve, add_sleeve; rationale plus cited evidence), `proposals` / `show-proposal`, and a person's `approve` / `decline` (`--confirm` on every mutation; YOLO never covers this lane; creation starts at revision 1; later edits name the current `revision`). It has no deployment or order authority ([ADR 0088](decisions/0088-portfolio-model-and-portfolio-backtest.md), [ADR 0091](decisions/0091-portfolio-deployment-limits-and-manager-proposals.md)): proposals auto-apply only inside the manager's permissions (pause a running sleeve; a paper rebalance within the rolling weekly budget), live rebalances, resumes, and new sleeves always wait for a person, and there is no order proposal. Portfolio backtests run each sleeve through the unified model on `weight × capital` over one common window and combine them; sleeves are simulated independently and portfolio caps are not simulated.
- `thytrader-runtime portfolio-*` — deploy a portfolio: `portfolio-start --revision N` (one bot per sleeve tagged `portfolio_id`; paper cash or live allocated capital = weight × capital; planned for every sleeve, so one refusal starts nothing; live `--i-understand-live`), `portfolio-pause|resume|stop [--sleeve-id] [--flatten]`, `portfolio-reset-breaker` (always `--confirm`), and read-only `portfolio-status`. A live portfolio's sleeve allocations count as risk-policy allocation membership for its bots; portfolio caps and latched daily-loss / drawdown stops bind every sleeve with explicit reason codes (`PORTFOLIO_TOTAL_EXPOSURE_LIMIT`, `PORTFOLIO_ASSET_EXPOSURE_LIMIT`, `PORTFOLIO_BREAKER_LATCHED`, `PORTFOLIO_DAILY_LOSS_STOP`, `PORTFOLIO_DRAWDOWN_STOP`) in risk verdicts and the decision timeline ([ADR 0091](decisions/0091-portfolio-deployment-limits-and-manager-proposals.md)).
- `thytrader-playbook` — sequences existing CLIs for data → research → optional paper (`--confirm` forwarded; never live).

- `thytrader-memory` — journals, why-trade review, sentiment/pattern hooks, monitor, notify, and
  fail-closed experiential training (`--confirm`; YOLO never covers this lane).

Portfolio `briefing` marks open sleeve books from the same decision journal as deployment reads,
with verified entry fees and net unrealized PnL even when `--decisions-per-sleeve 0` omits recent
decisions. Missing marks or fee evidence remain null. Exposure and reserved buying power count
entry remainders, excluding verified exit intents; missing intent evidence stays conservatively
counted. These corrections preserve the existing response schemas and confirmation gates.

In-app operator chat is a loopback UI (the Agent side panel on every page, or `/chat` as the full
page; [ADR 0079](decisions/0079-four-destination-shell-agent-panel-palette-tokens.md)) and
`/api/v1/operator-chat` over those same lanes ([ADR 0051](decisions/0051-in-app-operator-chat.md)). The user pastes **their** LLM API key; that
is not Coinbase. `thytrader-operator chat-status` is HTTP-only and never prints the key. Chat is
not a seventh skill lane.

Paper/live required-clock loaders use the research closed-bar mapping to cover warmup before
the first decision's previous mapped HTF or extra-indicator bar, including clock rollovers.
Their window remains deployment-anchored across restart. This does not waive completeness checks:
an archived catalog success cannot clear an execution mismatch. Diagnose through operator/runtime
reads, repair the cause, then explicitly resume in the runtime lane with the existing confirmation
and live acknowledgement gates and verify a fresh decision.

Lifecycle supervision scopes every product independently between bars, on a data gap, and
during cold-cache warming. Deliberate pauses remain paused; an already recorded live signal
exit, reached time exit, or flatten continues from a verified traded close without signal
reevaluation or decision-cursor advancement. Warming does not authorize a replacement bracket
that reverses that exit. Atomic fill transactions persist the focused runtime transition and
shared cash/fees together; the parent phase aggregates siblings without moving its cursor.

Canceled-order executions still await applied REST-fill coverage, even when only some fragments
are published. Applied entry economics with unprojected inventory are unresolved ledger evidence,
not a display-message predicate: a later read fault, restart, or cleared `mismatch_detail` cannot
prove flatness or authorize removing protection. Read `thytrader-runtime show UUID` and
`thytrader-operator reconciliation` and report the fault; do not invent a stop/quantity, rewrite
fills/fees, or resume a deliberately paused book ([ADR 0110](decisions/0110-stopped-lifecycle-reconciliation.md)).
An occupied product runtime (`open` / `pending_exit`) without its own position is likewise
unresolved (`runtime_position_unresolved`), even with surviving sibling inventory and regardless
of running/paused/stopped status. Reporting exposes these books as `unknown` / `open_unverified` with nullable protection quantities
and aggregate PnL/equity/exposure. Bounded summaries cannot certify flatness or full accounting;
valid focused book evidence is not shared-account completeness. Readiness inventory and venue
`managed_listing` separate read `status` from `accounting_status` (`complete` / `unresolved` /
`unavailable`) and `unresolved_deployment_ids`. `BOOK_ACCOUNTING_UNRESOLVED` /
`MANAGED_ACCOUNTING_UNRESOLVED` preserve null dependent capacity and affected asset differences
(`managed_unknown`), not fake zero/free capacity/foreign holdings. Independent balances, quotes,
assets and order checks remain known when proved. Protection/trigger incidents do not recover
just because positions disappear or executions become terminal. Prior opening proof/profits
cannot repair projection. Portfolio deployment/briefing readers qualify sleeve, breaker, exposure
and briefing performance with `accounting_complete`; breaker/exposure list affected deployment IDs.
Dependent current equity/PnL/exposure/return/drawdown are null, not zero. Run history includes
stopped current-run books; exposure also qualifies older occupied/residual books. Independent
allocations, baselines, historical evidence and resolved projected books/assets remain available.
Other portfolio/mode/quote scopes are not combined; a quote exposure subtotal cannot certify full
run accounting. No reporting flag resets latches, changes admission, or grants order authority. Full performance reports use `ACCOUNTING_UNRESOLVED`; confirmation
gates and read-only authority are unchanged.

Order-state provenance is independent of local row recency: `venue_observed_at` is persisted
only after identified live order reads, with legacy rows unknown until reconciliation. Do not
read local `updated_at` as venue verification ([ADR 0119](decisions/0119-venue-order-observation-provenance.md)).
Protection evidence uses `observation_source: venue_order_state` and `freshness: recent_venue`
only for actual identified OPEN status receipts within 120 seconds, with each contributing
quantity checked separately. `observed_at` is the latest relevant receipt; `verified_at` is the
oldest contributing fresh receipt (only that fraction if cover is partial). UNKNOWN/error reads
clear the evidence; local writes cannot renew it. Legacy/local evidence remains `persisted_order`
and unknown. `geometry_basis` names a persisted submitted-geometry check, **not** an independent
venue geometry audit or complete account reconciliation. UI fresh order state remains amber;
do not convert `covered` into an audited venue guarantee
([ADR 0112](decisions/0112-quantitative-protection-evidence.md)).

Judge configured market-data coverage by `watch_complete`. For a watched target, catalog, ingest, and gap payloads make `complete` watch-relative and keep `island_complete`; coverage is `watch_covered_candle_count` of `watch_expected_candle_count`. Catalog `watch_sparsity` is `gapped` when the watch is incomplete. Thin markets carry flat no-trade bars (`synthetic_no_trade_intervals`), and `history_floor_at` marks only a proven listing ([ADR 0095](decisions/0095-sparse-markets-no-trade-bars-listing-floors.md)). `inspect-gaps` may return `truncated=true` with a partial `gap_summary` when a server-side budget stops the scan ([ADR 0072](decisions/0072-catalog-health-bounded-gaps-self-complete-ingest.md)). `GET /api/v1/market-data/datasets` lists fingerprint-addressed island publications only. `/datasets/latest` and operator `data-catalog` are catalog-grade (structural checks, stat-identity cache, under a second warm); binding a dataset to a backtest, study, or deployment re-verifies its exact content fingerprint ([ADR 0085](decisions/0085-fast-research-ingest.md)). A CLI that prints `Timed out after N s waiting for the ThyTrader API` gave up waiting on a busy API; for a mutation, read state back before retrying.

Every HTTP command preflights `/health/ready` and fails closed on a missing or unequal ops contract. Matching package version `0.1.0` is not current-image evidence.

Outputs support both human-readable text and a documented JSON schema. Commands return meaningful exit codes so agents can distinguish healthy, degraded, and failed states.

## Safety model

The first operator contract and skill are read-only.

They must not:

- print API keys or private-key material;
- expose raw environment values;
- include unnecessarily precise account identifiers in support bundles;
- place, edit, or cancel orders;
- arm live trading;
- change strategy/risk configuration;
- bypass the API by reading or mutating database tables;
- treat missing telemetry as proof of health.

Future mutation tools must still be separate, narrowly scoped, auditable, idempotent where
applicable, and confirmation-gated. A read-only skill must never silently gain mutation
capabilities. Research, data, runtime, playbook, and memory already follow that split.

### Research mutation boundary

The browser strategy workspace ([ADR 0080](decisions/0080-per-strategy-workspace-build-test-run-why.md),
[ADR 0082](decisions/0082-strategy-root-mutable-strategies-auto-snapshots.md))
lives at `/strategies/{strategy_id}` (Build), `/test`, `/run`, and `/why`. A strategy is one
mutable object: Build loads `GET /api/v1/strategies/{strategy_id}` and saves in place with
`PUT /api/v1/strategies/{strategy_id}` (`{document, revision}`); a stale revision is HTTP 409
`strategy_revision_conflict` and is never overwritten. Invalid work in progress saves with its
`validation` result; Test and Run refuse to start (HTTP 422 `strategy_invalid`) until the current
definition validates. There are no drafts, publish, versions, or archive. Old `?version=` and
fingerprint deep links, `/research?strategy=`, and `/deploy?strategy=` resolve the owning strategy
(via `GET /api/v1/strategies/snapshots/{strategy_fingerprint}`) and redirect to the workspace.
Result and bot rows show **Current rules** or **Earlier edit** by comparing their snapshot
`strategy_fingerprint` to the strategy's `current_fingerprint`; Earlier edit offers a "What
changed" diff. The browser uses only these HTTP routes; agents keep the CLI lanes. The browser
library pages with a cursor, offers per-row delete and a checkbox bulk delete
(`POST /api/v1/strategies/bulk-delete`, dry run first, then `confirm: true`), and marks a
later-page failure as incomplete rather than calling the partial result
empty. The CLI still follows its bounded `--limit` / `--cursor` contract. There is one backtest
model, `engine: "thytrader-backtest"` ([ADR 0083](decisions/0083-unified-backtest-model.md)), and no
engine selector: `POST /api/v1/backtests` and study plan/submit bodies reject a
removed engine-version field with HTTP 422, and take an optional disclosed spread stress
`spread_bps` (default `"0"`). `GET /api/v1/research/backtest-model` (CLI
`thytrader-research backtest-model`) returns `{engine, decision_record, honesty, assumptions}`, the
same assumptions the browser's **How backtests simulate** disclosure renders.

The browser runtime surfaces compose the same runtime routes the `thytrader-runtime` lane uses and
add no endpoints: **Portfolio** (`/deployments`, paged `GET /api/v1/deployments`, with header
counts and per-mode, per-quote capital totals derived from the `capital` and `ledger` fields),
**bot detail** (`/deployments/{id}`: pause, resume, managed stop or `?flatten=true`, breaker latch
reset, cursor-paged orders/fills, the per-bar Decisions timeline from
`GET /api/v1/deployments/{id}/decisions` with trade reasons by `deployment_id` merged into its rows),
and **Trade** (`/trade`,
`POST /api/v1/discretionary-orders`). Every live mutation in the browser (arm, resume, place-order)
sends `i_understand_live: true` only after its dialog's explicit "real orders" checkbox. Pages that
show live exposure or compose a live order display an amber `LIVE:` strip and frame. Agents keep
using the CLI lanes; nothing here is an agent-only contract.

The earliest permitted mutation surface is limited to research artifacts:

- create, save (revision-guarded), import, clone, or delete a strategy;
- submit an idempotent backtest that names a strategy by `strategy_id` and a verified dataset (the
  server snapshots the current definition and returns its `strategy_fingerprint`);
- retrieve immutable results for comparison.

Strategy HTTP surface ([ADR 0082](decisions/0082-strategy-root-mutable-strategies-auto-snapshots.md)):

| Method and path | Purpose | Notable errors |
|---|---|---|
| `GET /api/v1/strategies?limit=&cursor=` | Library page (newest-updated first; `valid`, `current_fingerprint`, newest `backtest`, `paper_live`, `active_deployment_count`) | 400 malformed cursor |
| `POST /api/v1/strategies?product_id=&timeframe=&template=` | Create from a template (201) | 400 bad template/product/timeframe |
| `GET /api/v1/strategies/{strategy_id}` | Document, `strategy` (null when invalid), `validation`, `revision`, `current_fingerprint` | 404 `strategy_not_found` |
| `PUT /api/v1/strategies/{strategy_id}` | Save `{document, revision}` in place; invalid documents allowed | 409 `strategy_revision_conflict` (`current_revision`), 422 `strategy_document_invalid` |
| `DELETE /api/v1/strategies/{strategy_id}` | Hard delete with cascade; returns `counts`, `risk_policy_republished` | 404, 409 `strategy_has_active_deployments` (`deployment_ids`) |
| `POST /api/v1/strategies/bulk-delete` | `{strategy_ids, confirm, dry_run}`; per-id `outcome` (`deleted` / `would_delete` / `blocked` / `not_found` / `failed`) | 400 `confirmation_required` |
| `POST /api/v1/strategies/{strategy_id}/clone` | Copy into a new identity (201) | 404 |
| `POST /api/v1/strategies/import` | `{document}` → new strategy with a fresh id (201) | 422 `strategy_document_invalid` |
| `GET /api/v1/strategies/snapshots/{strategy_fingerprint}` | Snapshot definition, owning `strategy_id` (null if deleted), `is_current` | 404 `strategy_snapshot_not_found` |

`POST /api/v1/backtests`, `POST /api/v1/research/studies` (and `/plan`), and
`POST /api/v1/deployments` take `strategy_id` (studies also `candidate_strategy_ids` and
`markets[].strategy_id`) instead of fingerprints and fail with 404 `strategy_not_found` or 422
`strategy_invalid` (`issues`). `GET /api/v1/backtests`, `GET /api/v1/research/studies`,
`GET /api/v1/research/jobs` (required), and `GET /api/v1/deployments` accept `?strategy_id=`;
backtest list entries and job records carry `strategy_id` (backtest detail responses carry only the
snapshot `strategy_fingerprint`; resolve it with `GET /api/v1/strategies/snapshots/{fingerprint}`).
Start responses (201 and async 202) return `strategy_id` and the snapshot `strategy_fingerprint`.

Research starts may omit what the server can bind exactly
([ADR 0089](decisions/0089-agent-research-ergonomics.md)). Omitted dataset fingerprints (primary,
HTF, extra indicator clocks, additional instruments, and per-market study datasets) bind the newest
complete catalog dataset per product and clock from the configured ingestion provider; a clock with
no dataset is HTTP 422 `datasets_missing` with `detail.missing[]` and the `thytrader-data` commands
that create it. Study plan/submit bodies may omit both `evaluation_start` and `evaluation_end` to use
the common covered window of every child. Every start response echoes `bound_datasets`
(`product_id`, `timeframe`, `role`, `dataset_fingerprint`, `source: request|latest_catalog`), and
study responses echo the exact `evaluation_start` / `evaluation_end`; the bound values are part of
the run and request fingerprints. A cross-market study may name one `strategy_id` plus
`markets[].product_id`: the server records a per-market variant snapshot of that strategy for each
product (keeping its `strategy_id`, like sweep variants). Sweeps and WFO allow 64 candidates and 512
child windows as async jobs (`?async=true`, planned before queuing) and 8 candidates and 128 child
windows synchronously (422 `study_budget_exceeded` otherwise). The operator `products` report
carries each product's `price_increment`, `base_increment`, `quote_increment`, `base_min_size`,
`quote_min_size`, `status`, and `alias`, and `watch-add` answers an unverifiable product catalog with
a retryable HTTP 503 instead of a false "not enabled" 400.

Research runs in the `research-worker` service, never in the API
([ADR 0092](decisions/0092-research-worker-pool.md)). Every backtest, study, and portfolio backtest
is a durable job: `queued` (waiting for one of `THYTRADER_RESEARCH_WORKER_COUNT` workers, default
2), then `running` under a renewed lease, then `completed`, `failed`, `cancelled`, or `expired`.
`?async=true` answers 202 once the API has pinned the strategy snapshots, datasets, and bounds.
Async study planning runs in the leased worker; acceptance is not plan validation. Inspect a
failed job's `failed_phase: plan`, `error_code`, and `failed_detail`, or call `plan-study` first
([ADR 0103](decisions/0103-worker-planned-async-studies.md)); `thytrader-research submit-study --async` waits up to 30 s, and
`--submit-timeout-seconds` overrides it ([ADR 0097](decisions/0097-runtime-parity-and-observability.md)). A synchronous `POST /api/v1/backtests` or
`POST /api/v1/research/studies` waits up to `THYTRADER_RESEARCH_SYNC_WAIT_SECONDS` (25 s) and then
answers 201 with the same body as before, the same 422 (`backtest_window_rejected`,
`study_window_rejected`, `study_budget_exceeded`) or 503, or 202 with the still-running job and
`sync_wait_seconds`; poll `GET /api/v1/research/jobs/{job_id}` and never resubmit a running job.
Job records carry `attempts` (claims; a crashed worker's job is re-queued, at most 3 attempts) and
`error_code` (`research_worker_lost` when a job crashed its worker every time).
`thytrader-research list-research-jobs --strategy-id` lists a strategy's jobs. Queue depth,
per-worker RSS, and worker liveness are in operator health `payload.research_workers`.

Agent CLIs resolve the API base URL from `--base-url`, then `THYTRADER_API_BASE_URL`, then the
`THYTRADER_API_HOST` / `THYTRADER_API_PORT` settings (default `127.0.0.1:8200`; installs may override
the port), so scripts and raw `curl` calls should use `"$THYTRADER_API_BASE_URL"` rather than a
hard-coded port. CLI failures name the HTTP status and API `detail.code` with its message, or the
transport failure (timeout, unreachable origin, dropped connection), plus the next step; there is no
generic "failed safely" line.
Deployment responses carry `strategy_name` and `strategy_deleted`. Deleting a strategy is refused while any of
its bots is running or paused; stopped live books are kept and detached, never destroyed.

Each operation requires explicit user confirmation, returns stable artifact identities, and records an
audit event once audit recording exists. It may not deploy a strategy, start/stop paper execution,
arm live trading, submit/cancel Coinbase orders, modify risk limits, or perform direct storage access.

### Safe mode vs YOLO mode

**Default remains Safe mode:** mutations use `--confirm`, and live start, live resume, and live
place-order also require `--i-understand-live`. Over HTTP that acknowledgement is the strict
boolean `i_understand_live: true` on `POST /api/v1/deployments` (mode `live`),
`POST /api/v1/deployments/{id}/resume` (live books), and `POST /api/v1/discretionary-orders`
(mode `live`); without it the API returns HTTP 428 `live_acknowledgement_required`. Ops contract
`thytrader-ops-contract-v65` ([ADR 0078](decisions/0078-live-readiness-http-ack-venue-reload-definite-rejects.md),
[ADR 0082](decisions/0082-strategy-root-mutable-strategies-auto-snapshots.md),
[ADR 0083](decisions/0083-unified-backtest-model.md),
[ADR 0085](decisions/0085-fast-research-ingest.md),
[ADR 0086](decisions/0086-indicator-catalog-expansion-and-offset.md),
[ADR 0087](decisions/0087-per-bar-decision-timeline.md),
[ADR 0088](decisions/0088-portfolio-model-and-portfolio-backtest.md),
[ADR 0089](decisions/0089-agent-research-ergonomics.md),
[ADR 0090](decisions/0090-research-correctness-optional-take-profit-diagnostics.md),
[ADR 0091](decisions/0091-portfolio-deployment-limits-and-manager-proposals.md),
[ADR 0092](decisions/0092-research-worker-pool.md),
[ADR 0093](decisions/0093-signal-based-exits.md),
[ADR 0094](decisions/0094-research-honesty-and-agent-ergonomics.md),
[ADR 0095](decisions/0095-sparse-markets-no-trade-bars-listing-floors.md),
[ADR 0096](decisions/0096-reference-instruments.md),
[ADR 0097](decisions/0097-runtime-parity-and-observability.md),
[ADR 0098](decisions/0098-library-views-book-marks-portfolio-fills.md),
[ADR 0099](decisions/0099-operand-level-indicator-offsets.md); `backtest_engine:
"thytrader-backtest"`; `indicator_kinds`, `indicator_offset_runtimes`,
`indicator_operand_offset_runtimes`, `signal_exit_runtimes`,
`reference_instrument_runtimes`, and `max_reference_instruments`;
`decision_journals: ["paper", "live"]`; `portfolio_model`; `research_dataset_autobind` and
`study_budgets`; `take_profit_kinds`, `live_protection_kinds`,
`backtest_diagnostics`, `fee_suggestion_source`; `portfolio_deployment`, `portfolio_breakers`,
`portfolio_proposal_kinds`, `portfolio_briefing_contract`; `research_worker_pool`;
`research_honesty`, `strategy_library`, `portfolio_max_sleeves`, `portfolio_sleeve_operations`
([ADR 0094](decisions/0094-research-honesty-and-agent-ergonomics.md)); the `catalog_health` tokens
`no_trade_bars`, `listing_history_floor`, and `watch_relative_complete`
([ADR 0095](decisions/0095-sparse-markets-no-trade-bars-listing-floors.md));
`same_bar_exit_precedence` (`stop`, `take_profit`, `signal_exit`, `time_exit`) and
`runtime_observability` (`position_state`, `exit_in_flight`, `paper_live_fill_comparison`;
[ADR 0097](decisions/0097-runtime-parity-and-observability.md); plus `paper_protection_covered`,
`book_marks` and `portfolio_fill_comparisons`, with `strategy_library` adding `origin_filter`;
[ADR 0098](decisions/0098-library-views-book-marks-portfolio-fills.md); plus
`fee_adjusted_book_pnl`, [ADR 0100](decisions/0100-fee-adjusted-open-book-pnl.md), and
`capital_normalized_performance`, [ADR 0107](decisions/0107-capital-normalized-live-performance.md));
expected Alembic revision `0061`).

Research correctness ([ADR 0090](decisions/0090-research-correctness-optional-take-profit-diagnostics.md)):
`exits.take_profit` may be `{"kind": "none"}` (live protects such books with a Coinbase
stop-limit); strategy responses carry advisory `validation.warnings`; `GET
/api/v1/backtests/{fp}` returns `diagnostics` (the entry funnel, outside the result
fingerprint); `GET /api/v1/backtests/{fp}/signal-trace` pages the result's entry-condition trace
(`thytrader-research-evaluate`); `GET /api/v1/fees` suggests the account's reported Coinbase
rates (`suggestion_source: coinbase_account`) with the public schedule as context; and the
decision timeline records geometry/sizing refusals as `skipped` with exact reason codes.

Signal-based exits ([ADR 0093](decisions/0093-signal-based-exits.md)): an optional
`exits.signal_exit: {"when": <entry-grammar condition tree>}` closes an open position when it
matches on a closed bar after the fill bar, as a taker at that close (the time-exit convention) in
backtest, paper, and live. The mandatory initial stop still guards the book and wins a same-bar
tie. Backtest trades report exit reason `signal`, diagnostics add `exit_reasons`, signal traces add
`exit_condition`, and results disclose `signal_exit_at_close`. Live cancels protection before the
marketable cover and keeps exiting through a pending cancel (`signal_exit_bar` on the position);
decision rows carry `exit_reason: signal` and the evaluated `exit_rule`. Template
`ema-trend-hold` holds a trend until EMA(20) crosses back below EMA(100).

Reference instruments ([ADR 0096](decisions/0096-reference-instruments.md)): an optional
`data_requirements.reference_instruments: [{"id": "btc", "product_id": "BTC-USDC", "timeframe":
"1d"}]` (at most 3, same quote currency, timeframe equal to or a coarser integer multiple of the
strategy's) lets indicators declare `"source": "btc"` and read that series' closed bars, so a rule
can gate on another market ("alts only while BTC 1d close > EMA(100)"). Only the last reference bar
closed by each decision close is visible; reference warmup is derived from its indicators. Backtests
and studies bind reference datasets automatically (`bound_datasets` rows with `role: "reference"`
and `reference_id`; pin with `reference_dataset_fingerprints`), and cross-market studies keep the
reference fixed. Paper and live load reference bars every cycle and skip entries fail closed with
`skip_reason` `reference_data_stale` / `reference_data_missing`; `POST /api/v1/deployments` (and
portfolio sleeves) return 409 naming the `thytrader-data watch-add` command until each reference
series is on the enabled watchlist. References are never traded: no cross-instrument orders, one
traded instrument per strategy. Template `btc-regime-gate`.

Research honesty and ergonomics ([ADR 0094](decisions/0094-research-honesty-and-agent-ergonomics.md)):
every backtest result carries `window` (`evaluation_start`, `evaluation_end`, `warmup_bars`, first
and last evaluated bar), derived from its run outside the result fingerprint — pin
`evaluation_start` / `evaluation_end` when comparing strategies, because omitted bounds start after
each strategy's own warmup. Study summaries give every `window_pnl` row its candidate's
`axis_values` and bounds, add per-candidate sums in `candidates[]`, and return a thinned stitched
OOS path. Strategy writes nest `validation` exactly like `show-strategy` (top-level copies are
deprecated) and print an `INVALID draft` line on stderr; issue `loc` values are document paths
such as `entry.when.all[0].left.input`. Decimal request fields accept JSON numbers with unchanged
fingerprints. Decision operands carry 12 significant digits. `GET /api/v1/strategies?tag=`,
`list-strategies --tag`, `bulk-delete-strategies --tag`, `clone-strategy --name`,
`thytrader-portfolio delete`, `create` with limits and manager settings, and `add-sleeves --file`
(`POST /api/v1/portfolios/{id}/sleeves/batch`, one revision, at most 32 sleeves) remove most bulk
chores. `GET /api/v1/strategies?origin=research|operator|all` and `list-strategies --origin` split
agent research (`claude-research` / `research-*` tags) from the operator's own strategies; combine
it with `tag` ([ADR 0098](decisions/0098-library-views-book-marks-portfolio-fills.md)).
`thytrader-portfolio create --file portfolio.json --confirm` also accepts optional initial
`sleeves: [{"strategy_id": "UUID", "weight_fraction": "0.5", "note": "optional"}]`, alongside
the portfolio settings ([ADR 0101](decisions/0101-atomic-portfolio-creation-with-sleeves.md)).
One `POST /api/v1/portfolios` validates the full definition and writes every row and journal entry
at revision 1, or writes nothing. At most 32 distinct strategies; the existing market/quote and
exact weights-plus-reserve checks apply. Omitted/empty sleeves keep empty creation working.
The v61 capability `portfolio_sleeve_operations: ["batch_add", "create_with_sleeves"]` prevents
using this body against a stale image. Creation does not deploy, arm, or place orders; confirmation
and the separate runtime lane remain required. Creation takes no `revision`; later edits do.
Book marks ([ADR 0098](decisions/0098-library-views-book-marks-portfolio-fills.md)):
`GET /api/v1/deployments/{id}` positions and `GET /api/v1/portfolios/{id}/deployment` sleeve
`books[]` carry `mark_price` (the last evaluated bar's close from the decision journal),
`marked_at`, and gross `unrealized_pnl` (null without a journaled close).
The same marked rows add `entry_fees` and `unrealized_pnl_net`: paid entry fees allocated
to held inventory and gross PnL minus those fees. Both summary and full deployment detail
use a bounded local read of at most 1001 applied product fills since the entry bar; more
than 1000 or a quantity/average-price mismatch returns null. Sleeve books use the same fold
on their already-loaded fills. Partial exits allocate fees proportionally and adds accumulate
them. Future exit fees are excluded; null is unknown, never assumed free execution
([ADR 0100](decisions/0100-fee-adjusted-open-book-pnl.md)).
Deployment detail (summary or full) derives `ledger.mark_complete`, `marked_exposure`, net PnL,
and return from the same per-product journal marks. Operator `strategies` / `runtime` use them
for `ledger_mark_complete`. Every open book needs its own close: a missing mark or journal
outage leaves aggregate PnL/exposure unknown. These reads stay local and bounded; the separate
operator `performance` report uses market-data closes.
`GET /api/v1/portfolios/{id}/fill-comparisons` returns the operator report's
`paper_live_fill_comparisons` rows for twins of that portfolio's sleeves. A resolved open paper
book's `protection_status` is `covered` on every read.
Sparse markets ([ADR 0095](decisions/0095-sparse-markets-no-trade-bars-listing-floors.md)): Coinbase returns no candle for an interval without
trades. The worker publishes each confirmed one as a flat zero-volume bar. Dataset manifests count
them (`synthetic_no_trade_intervals`), and backtests whose window holds one disclose
`synthetic_no_trade_bars`. Paper and live fill a bar missing between two traded bars the same way
and mark the decision row `no_trade_bar: true`; a missing newest bar still pauses the book
(`data_gap`). `history_floor_at` marks only a proven listing. Catalog, ingest, and gap payloads make
`complete` watch-relative (`island_complete` keeps the dataset fact) and report coverage as
`watch_covered_candle_count` of `watch_expected_candle_count`. Alembic `0059` cleared every
pre-existing floor.

**YOLO mode (shipped, default OFF)** is an operator-enabled opt-in so agents can skip per-action
confirmation on **allowed** surfaces when the operator wants maximum automation friction removed.
See [ADR 0034](decisions/0034-phase-12-agent-orchestration-yolo.md),
[ADR 0043](decisions/0043-yolo-live-skip-confirm.md), and
[ADR 0055](decisions/0055-yaml-settings-runtime-reloadable-yolo.md).

Shipped constraints:

- Opt-in configuration (`thytrader.yaml` `yolo.enabled` plus `yolo.tiers`; leftover
  `THYTRADER_YOLO_ENABLED` / `THYTRADER_YOLO_TIERS=paper` still parse). YAML wins leftover env
  ([ADR 0055](decisions/0055-yaml-settings-runtime-reloadable-yolo.md)). Never the silent
  default for observation skills.
- Scope tiers: `data`, `research`, `paper`, and `live` are independently eligible. Live YOLO
  skips `--confirm` only on live start/pause/resume/stop and never skips `--i-understand-live`.
  Live `place-order`, `set-risk-policy`, `set-settings`, Coinbase credential set/clear, `--local` research, and memory stay confirmation-hard-gated.
  Paper YOLO never covers live.
- Audit every skipped confirmation (`confirm_skipped`) or fail closed.
- Do not collapse operator / data / research / runtime authority into one unrestricted skill.
  The playbook never starts live, including when YOLO advertises `live`.

See [agent-driven platform gap plan](plans/2026-09-12-agent-driven-platform-gap-plan.md).

### Portfolio visibility then research

Account balances and portfolio history are **`GET /api/v1/portfolio`** and
**`GET /api/v1/portfolio/history`** (loopback; no `thytrader-operator` CLI subcommand today).
Deployment inventory (quantities, orders, fills, capital, lifecycle/latch state) is
**`thytrader-runtime show`** / **`GET /api/v1/deployments/{id}?detail=full`**. Operator
`performance --deployment-id` reports the snapshot instrument quote for strategy deployments
or the product quote for discretionary books. When quote provenance cannot be verified,
`payload.currency` is null with a partial-result warning; never silently interpret it as USDC.
Default `detail=summary` omits historical orders and fills; paginate **`/fills`** and **`/orders`**
([ADR 0074](decisions/0074-multi-book-ledger-bounded-reads.md)). Live sizing uses the nested
`capital` block; lifecycle fields (`lifecycle_command`, `daily_loss_latched`, `drawdown_latched`,
`revision`, `worker_lease_held`) are top-level on the same payload
([ADR 0064](decisions/0064-deployment-http-lifecycle-and-breaker-latch-reset.md),
[ADR 0065](decisions/0065-deployment-capital-accounting-http.md)). Operator `strategies` /
`runtime` expose redacted `books[]` only. Numbered recipe:
[`docs/agent/portfolio-research-ops-playbook.md`](agent/portfolio-research-ops-playbook.md).

### Verified daily-risk evidence

Admission and runtime breakers reload fresh unfiltered accounting for all retained books;
a current product overlay or cached portfolio never replaces sibling fill evidence.
`capital.utc_day_open_equity` remains preserved legacy data, not verified midnight equity.
Nullable `capital.risk_day_open_evidence` contains `source: per_product_applied_fills_v1`, UTC
`day_start`, exact decimal `equity`, `fills_fingerprint`, and per-product midnight `marks[]`
(`product_id`, `closes_at`, exact `price`). Complete applied fills reconstruct opening cash and
signed quantities separately; genuine flat midnight needs no price. Nonzero overnight inventory
needs actual closed midnight prices, recoverable through an exact complete hourly range.
Readers revalidate against current fills and UTC day; old evidence or an old stamp cannot bypass
`BREAKER_MARK_MISSING`. Maintenance, late restart and latch reset never invent opening equity.
See [ADR 0120](decisions/0120-verified-risk-opening-evidence.md).

Primary operator `risk` reports `max_order_quantity`, `max_order_notional_quote`, and
`min_available_quote_reserve` as nullable configuration strings, never observed balances.
`PUT /api/v1/risk-policy` and `set-risk-policy --confirm` replace the whole policy: omission
unsets optional bounds in the successor. Read the current policy and resupply bounds to retain
them; immutable historical documents and compiled fingerprints are unchanged. The current
frontend reads risk policy; it has no separate direct risk-policy editor/publication path.

### Orchestration skill

`thytrader-playbook` sequences data → research → optional paper by calling existing CLIs. It
inherits the same Safe / YOLO confirmation rules and must not grant live authority by inheritance.

### Memory skill

`thytrader-memory` records origin-attributed journals, sentiment/pattern hooks, notify requests,
why-trade review, and trains a fail-closed advisory model from attributed local journals.
`--confirm` is always required. YOLO never covers this lane. Operator `monitor` and
`trade-reasons` are read-only. Place-order `--note` is the first why-trade note; later notes use
`add-trade-reason-note --confirm`. Training consumes `JournalEntry` as stored.

### Durable safety alerts

Use `uv run thytrader-operator alerts`, `GET /api/v1/operator/alerts`, or `/alerts`
for durable local safety observations ([ADR 0115](decisions/0115-durable-safety-alerts-and-supervision.md)).
This is read-only; control stays in the separately confirmation-gated runtime lane.
Recovery is per-check and requires complete evidence. Missing/stale/partial snapshots,
storage failures, unavailable/warming or insufficient candles, and non-authoritative
subset inventories never mean healthy. A consumed live stop remains sticky until
terminal/fill/removal evidence clears it; a price rebound is not recovery and the
alert does not authorize a market exit. Future-skewed leases mean unknown age.

Counts/status use the full open inventory even when the displayed feed is bounded.
Repeated verified failures can fence a new-entry pause without overwriting a stop,
operator pause, latch, strategy identity, or unrelated mismatch. That pause survives
restart; ambiguous no-op passes do not reset the failure count, and maintenance,
reconciliation, and exits continue. It is never auto-resumed.

`notify_provider=none` explicitly records skipped/disabled delivery and does not
spend retry attempts. Delivery is independent of execution cycles. Durable claims
prevent concurrent sends while the claim is valid; retries reuse the alert UUID.
A send/ack crash can still duplicate an external webhook, so receivers must
idempotently deduplicate. Bounded retries can exhaust without external receipt.
Do not infer exactly-once delivery or invent a destination.

### Fleet controls

Mode-wide controls live in the confirmation-gated runtime lane, not the read-only operator lane
([ADR 0117](decisions/0117-truthful-inventory-and-fleet-controls.md)): `uv run thytrader-runtime
fleet-preview|fleet-status|fleet-disarm|fleet-stop|fleet-flatten|fleet-rearm`, or
`GET /api/v1/fleet-control`, `GET /api/v1/fleet-control/preview?action=&mode=` and
`POST /api/v1/fleet-control/{disarm|stop|flatten|rearm}`. Disarm only inhibits new starts and
entries; stop is a managed shutdown that keeps protection; flatten is explicit; rearm clears the
latch without resuming books. Every mutation requires `confirm`, the previewed deployment
revisions (and latch revisions for disarm/rearm), and an idempotency key; live flatten and live
rearm also require the live acknowledgement. YOLO never covers them. An agent must not refresh
revisions to replace the person's consent, and a partial result does not mean positions are flat.
The [runtime skill](../skills/thytrader-runtime/SKILL.md) has the exact flags.

## Stable diagnostics schema

Every machine-readable report should include:

- schema version;
- application version;
- timestamp and timezone;
- overall status: healthy, degraded, or failed;
- component statuses and stable reason codes;
- data freshness/coverage metadata;
- redaction metadata;
- partial-result warnings;
- recommended next diagnostic action.

Performance reports must state timeframe, strategy snapshot fingerprint, dataset/source, currency, fee treatment, and whether values are backtest, paper, or live.

## Skill packaging

The intended layout is:

```text
skills/
├── thytrader-operator/
│   ├── SKILL.md
│   └── references/
│       ├── diagnostics-api.md
│       ├── report-schemas.md
│       └── operator-report-v1.schema.json
├── thytrader-data/
│   └── SKILL.md
├── thytrader-research/
│   └── SKILL.md
├── thytrader-runtime/
│   └── SKILL.md
├── thytrader-playbook/
│   └── SKILL.md
└── thytrader-memory/
    └── SKILL.md
```

`thytrader-operator/SKILL.md` documents commands that exist: `thytrader-operator` and
`GET /api/v1/operator/*`. `thytrader-data/SKILL.md` documents confirmation-gated watchlist and
queued worker ingest. `thytrader-research/SKILL.md` documents `thytrader-research` with
`--confirm` for mutations. `thytrader-runtime/SKILL.md` documents confirmation-gated paper/live
control, discretionary `place-order`, risk-policy publication (`set-risk-policy --confirm`, including optional daily-loss / drawdown / rate / collar flags, `--allow-intra-strategy-pyramiding`, and optional absolute `--max-daily-loss-quote` / `--max-portfolio-exposure-quote` / `--max-venue-order-actions-per-minute`), and write-only Coinbase credential show/set/clear (`--confirm` always on set/clear; `--private-key-file`; YOLO never covers credentials). `thytrader-playbook/SKILL.md` sequences those CLIs and never
starts live. `thytrader-memory/SKILL.md` documents journals, why-trade review
(`list-trade-reasons` / `show-trade-reason` / `add-trade-reason-note`), sentiment/pattern hooks,
monitor, notify, and `train` / `list-models` / `show-model` with `--confirm` (YOLO never covers
that lane).
Product of record is `skills/`;
`.cursor/skills/` contains pointers for Cursor auto-load.

Operating a running instance is a separate workspace: open [`ops/`](../ops/README.md), not the git
root. Contributor GitNexus workflow stays in root `AGENTS.md`. Operating agents must not edit
`src/`, Compose, Dockerfiles, Alembic, or tests.

The operator skill tells agents to:

1. Verify version and connectivity.
2. Start with read-only health/configuration checks.
3. Gather the minimum required report.
4. Preserve mode, timeframe, and strategy snapshot (`strategy_fingerprint`) context.
5. Correlate performance with fees, data quality, risk events, and execution anomalies.
6. Redact before returning diagnostics.
7. Clearly separate verified findings from hypotheses.
8. Stop and request explicit authority before any state-changing action.

## Testing requirements

- Contract tests for every JSON report.
- Golden tests for redaction.
- Tests proving secrets cannot appear in output.
- Compatibility tests between the skill's documented schema and current CLI/API.
- Failure-mode tests for database, worker, Coinbase, and market-data outages.
- Tests proving read-only commands cannot mutate orders, strategies, or runtime state.

## Skill evolution by capability

| Capability available | Supported agent authority |
|---|---|
| Supported read-only diagnostics | `thytrader-operator`: health, configuration validity, market-data quality, strategy library state, backtest/paper/live performance slices, reconciliation, runtime watch (redacted `books[]`), persisted research-study catalog, why-trade journals, the per-bar decision timeline (`decisions`), durable safety alerts (`alerts`), and a redacted support bundle. Account balances and portfolio history: `GET /api/v1/portfolio` and `/history` (not an operator CLI subcommand). Deployment quantities: `thytrader-runtime show`. HTTP by default. |
| Supported strategy/backtest mutation contracts | `thytrader-research`: confirmation-gated strategy create/save/import/clone/delete, backtest submission by `strategy_id` (including `additional_instrument_datasets` for extra covered products), composed OOS / walk-forward / cross-market / sweep / WFO studies, and persisted study catalog reads. HTTP by default. |
| Paper runtime | Read-only paper-session status and fill-ledger PnL through the operator skill. Paper start/pause/resume/stop uses `thytrader-runtime` with `--confirm`. Optional `--maker-fee-rate` / `--taker-fee-rate` are documented paper assumptions ([ADR 0048](decisions/0048-paper-deploy-fee-fields.md)); omitted rates stay `0.001` / `0.002`. `thytrader-playbook` may start paper only and uses those defaults. |
| Guarded live execution | `thytrader-runtime start --mode live --confirm --i-understand-live` or, when YOLO advertises `live`, `start --mode live --i-understand-live` after an audited skip. Live `place-order` still needs `--confirm` and `--i-understand-live`. Live fills ingest through cursor-terminated List Fills and quarantine incomplete rows ([ADR 0059](decisions/0059-coinbase-list-fills-cursor-pagination.md)). Arming, cancellation of individual venue orders, configuration changes, and kill switches never inherit authority from an observation, research, or playbook skill. |
| Coinbase credentials | `thytrader-runtime show-coinbase-credentials` / `set-coinbase-credentials --private-key-file` / `clear-coinbase-credentials`. HTTP `GET/PUT/DELETE /api/v1/credentials/coinbase`. Presence flags only; GET never echoes secrets. Set/clear always `--confirm`. YOLO never covers this. Setting credentials does not arm live trading. LLM keys stay on `/chat`. |
| Experiential memory | `thytrader-memory`: confirmation-gated journals, why-trade review, sentiment/pattern hooks, notify, and fail-closed `train`. Operator `monitor` and `trade-reasons` are read-only. YOLO never covers this lane. Research `create-strategy --experiential-model-id` may merge the advisory into JSON (HTTP only). |
| In-app operator chat | Loopback `/chat` plus `/api/v1/operator-chat`. Uses the same HTTP skill routes. Mutations stay confirmation-gated; live still needs understand-live. LLM keys stay in the API process; Coinbase keys never go to the browser. |

The key principle: **agents should diagnose and explain first; trading authority is not a natural extension of observability.** Agent E2E as the primary surface ([ADR 0030](decisions/0030-agent-e2e-primary-surface.md)) does not collapse these lanes.


### Explicit paper/live twin metadata (ADR 0102)

Ops contract v63 advertises `runtime_observability: explicit_deployment_twins` and
`rule_matched_deployment_twins`, schema `0060`.
`GET /api/v1/deployments/{id}/twin` reads `{deployment_id, twin}` (null or the two member UUIDs
and `linked_at`). `PUT` takes only `{counterpart_deployment_id}`; `DELETE` requires the expected
counterpart UUID as `?counterpart_deployment_id=...`. Both directions address the same pair.

Agents invoke `thytrader-runtime show-twin ID`, `link-twin ID --counterpart-deployment-id ID
--confirm`, and `unlink-twin ID --counterpart-deployment-id ID --confirm`. These mutations are
always confirmation-gated, outside YOLO, and require no live acknowledgement. They grant no
lifecycle, arming, or order authority. Use identical pinned trading rules, primary market, and timeframe on
opposite-mode strategy bots. Incompatible pairs return 422; another partner or stale unlink 409;
missing deployment 404; storage outage 503. Repeating the same link/unlink is idempotent; after
an ambiguous response read the pair before retrying. Reports compare only saved pairs, up to 10
newest-linked first, including multiple pairs sharing a fingerprint. Read-only operator and
portfolio lanes cannot edit links. See the [runtime skill](../skills/thytrader-runtime/SKILL.md)
and [ADR 0102](decisions/0102-explicit-paper-live-twin-links.md).

Explicit twins may be strategy clones with server-verified identical pinned trading rules
(ADR 0105). Each comparison side names its actual snapshot fingerprint; pairing changes only
comparison metadata, never bot lifecycle or trading rules.

## Account reads and audit recovery evidence

[ADR 0108](decisions/0108-account-read-and-audit-failure-evidence.md) ships ops contract v65
(Alembic `0061`), with `exchange_read_failures` and `audit_failure_evidence` in
`runtime_observability`. `thytrader-operator exchange` exposes a nullable
`payload.failure` with operation, safe category, and HTTP status; health includes the
same safe summary. Raw exception messages and provider bodies are omitted.
Reconciliation lists individual failures from the newest 20 audit events, linking
`audit_event.event_id`, `occurred_at`, `action`, and matching recovery evidence.
Only known WebSocket connected events prove historical recovery; unrelated successes
do not resolve order failures. Recovered failures remain findings; current user-feed
health is independently inspected through `thytrader-operator runtime`.

Account GETs retry once after 0.5 seconds only for timeout/network or HTTP 502/503/504.
The repeated request is freshly signed on the same pagination cursor; exhausted failures
return no partial balances. Authentication, 429 rate limits, malformed responses and
pagination errors do not retry. Failed-read evidence includes `attempts` (1 or 2).
Order submissions and cancellations never use this retry helper.

Research reliability: [frozen campaigns, prospective validation, economic preflight,
stress assumptions, and bounded exports](user/research.md) are shipped through the research
HTTP/CLI lane and `/research` UI. They confer no deployment or order authority.

Backtest detail and bounded export projections now include optional `cost_attribution`
(`thytrader-cost-attribution-v1`); research `show-result` forwards it and operator
`performance --result-fingerprint` calculates it from verified evidence. The four
exact quote-currency totals are `fill_price_pnl_before_fees`, `entry_fees`,
`exit_fees`, and closed-trade `net_pnl`. Modeled spread/slippage already affect fill
prices. `accounting_residual` and `summary_net_pnl_delta` disclose ledger/summary
rounding differences. Source identities and the attribution's own fingerprint are
validated. Publication-time metadata lives outside canonical result bytes (Alembic
0064); missing legacy bounded metadata remains null with a warning. Full detail
computes it without mutating a publication. See the research skill for invocations.

The expected operational schema revision is `0066` (durable safety alerts). Backtest fee metadata remains the Alembic `0064` column.
Health rejects a different applied revision. A repository test compares the
advertised revision with Alembic head so a migration cannot silently ship a stale
health contract. Matching application versions alone remains insufficient.

Backtest tables scroll within their panels on narrow screens, and long ratios and
reconciliation values wrap. Agent reads and exports retain exact recorded decimals;
rounded monetary cards in the UI are a display convenience.
