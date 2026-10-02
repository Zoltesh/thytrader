# Agent and Operator Integration

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

- `thytrader-operator` — health, configuration, exchange, market-data, data-catalog, products, indicators, strategies, performance, risk, reconciliation, runtime, monitor, studies, trade-reasons, decisions, portfolios, support-bundle, schema-check, chat-status.

`uv run thytrader-operator indicators` lists the fail-closed catalog an agent may author: 53 kinds
grouped by `category` (trend, momentum, volatility, volume, statistical, price), each with its
`inputs`, every parameter's bounds, builder `default`, and one-line `help`, output `series`, the
`warmup` formula, and `default_warmup_bars`
([ADR 0047](decisions/0047-wider-fail-closed-indicator-catalog.md),
[ADR 0086](decisions/0086-indicator-catalog-expansion-and-offset.md)). Any declaration except
`constant` may add `offset` (0–500 completed bars of its own clock) to read an earlier bar, for
example the previous bar's Donchian channel for a breakout. Do not invent unlisted kinds.
- `thytrader-data` — watchlist, ingest, inspect-gaps, fill-gaps (`--confirm` on mutations; `watch-add`, `ingest`, and `fill-gaps` POSTs send installation Bearer when a token is resolvable). `ingest` and `fill-gaps` only queue work for an existing watch: an unwatched product/timeframe is HTTP 409 naming `watch-add`, and no watch is created. `watch-add --lookback-hours` ceilings run from 2160 (90 days) at `1m` to 87600 (10 years) at `2h`-`1d` ([ADR 0085](decisions/0085-fast-research-ingest.md)).
- `thytrader-research` — strategy list/show/create/save/import/clone/delete/bulk-delete and snapshot reads, backtests and composed studies by `strategy_id`, and persisted study catalog reads (`--confirm` on mutations). Multi-instrument documents bind extra products through `additional_instrument_datasets` on submit-backtest JSON (lexicographic `product_id`; omitted when empty) ([ADR 0056](decisions/0056-multi-instrument-documents-and-pyramiding.md)). Omitting both `evaluation_start` and `evaluation_end` on `submit-backtest` fills the common LTF+HTF (and extra-clock) covered intersection. `show-result` reports the snapshot's strategy clock, including `2h` and `4h`.
- `thytrader-runtime` — paper/live start, pause, resume, stop, on-demand place-order, and write-only Coinbase credential show/set/clear (`--confirm` unless YOLO covers that tier; live also `--i-understand-live`; `--side` long or short). Default stop is managed shutdown (keep protective brackets and residual occupancy); `--flatten` / `?flatten=true` marketably exits ([ADR 0058](decisions/0058-protection-lifecycle-accounting.md)). Paper start/place-order may pass `--maker-fee-rate` / `--taker-fee-rate` (documented assumptions; omitted paper uses `0.001` / `0.002`; live rejects the flags). Credential set/clear always need `--confirm` and `--private-key-file` (never a CLI secret). YOLO never covers credentials. Setting credentials does not arm live trading. `set-risk-policy --allow-intra-strategy-pyramiding` is required for paper/live same-side adds when the strategy also enables pyramiding ([ADR 0056](decisions/0056-multi-instrument-documents-and-pyramiding.md)). Live start and live `place-order` require a published risk policy; the compiled default cannot arm live (`LIVE_REQUIRES_PUBLISHED_POLICY`). Optional `--max-daily-loss-quote` / `--max-portfolio-exposure-quote` add absolute quote ceilings alongside the matching fraction, and optional `--max-venue-order-actions-per-minute` adds a combined entry+cancel budget that can only deny a new entry, never a cancellation or protective submission ([ADR 0063](decisions/0063-stage-5-release-discipline-ci-risk-defaults-rate-budget.md)). `list` / `show` return every product book (`positions`, `instrument_runtimes`, product-tagged orders/fills, `book_totals`). The singular `position` field is compatibility-only and always includes `product_id`; read `positions` for inventory ([ADR 0060](decisions/0060-multi-book-deployment-api.md)). `protection_status` is classified from verified attached-child coverage and venue-visible exits (`flat` / `covered` / `unprotected` / `unknown`); missing children are unprotected, not unknown ([ADR 0058](decisions/0058-protection-lifecycle-accounting.md)). Default `stop` is managed shutdown; pass `--flatten` or `?flatten=true` only to marketably exit then cancel remainders. Pause still maintains protection and does not reset breaker baselines. Operator `runtime` reports `lifecycle_command`, latches, `revision`, and `worker_lease_held`; `thytrader-runtime show` reports a `capital` block (`allocated_capital`, `venue_available_quote`, reserved/inventory/equity fields) separately from ledger `cash` ([ADR 0065](decisions/0065-deployment-capital-accounting-http.md)).
- Per-bar decision timeline (read-only; [ADR 0087](decisions/0087-per-bar-decision-timeline.md)): every paper and live strategy bot journals one `thytrader-bar-decision-v1` record per completed bar and covered product — `outcome` (`entry_signal`, `no_signal`, `holding`, `exit`, `entry_blocked`, `skipped`, `error`), a one-line `summary` (for example `No trade: RSI(14) 47.21 needs ≥ 50`), the evaluated rule tree with leaf values versus thresholds and the HTF filter, the risk verdict, `action` plus `intent_id`/orders/fills, skip/exit reasons, close price, and the end-of-bar position. Read it with `thytrader-runtime decisions UUID` / `GET /api/v1/deployments/{deployment_id}/decisions`, across a strategy's bots with `thytrader-runtime decisions --strategy-id UUID` / `GET /api/v1/strategies/{strategy_id}/decisions?deployment_id=`, or as operator report kind `decisions` (`thytrader-operator decisions`, `GET /api/v1/operator/decisions`). All accept repeated `outcome` filters and `cursor`/`next_cursor` paging, newest bar first. The in-app chat tool is `runtime_decisions`. Journaling never blocks or changes trading (failed writes are audited as `decision_journal_write_failed`); the journal keeps the newest 20,000 decisions per bot (one per bar and covered product) for at most 180 days.
- `thytrader-portfolio` — portfolio list/show/create/update, add-sleeve/remove-sleeve/set-weights, portfolio backtests (backtest/show-backtest/list-backtests), the journal, and the manager loop: read-only `deployment` and `briefing` (`thytrader-portfolio-briefing-v1`), `propose` (rebalance, pause_sleeve, resume_sleeve, add_sleeve; rationale plus cited evidence), `proposals` / `show-proposal`, and a person's `approve` / `decline` (`--confirm` on every mutation; YOLO never covers this lane; every mutation names the current `revision`). It has no deployment or order authority ([ADR 0088](decisions/0088-portfolio-model-and-portfolio-backtest.md), [ADR 0091](decisions/0091-portfolio-deployment-limits-and-manager-proposals.md)): proposals auto-apply only inside the manager's permissions (pause a running sleeve; a paper rebalance within the rolling weekly budget), live rebalances, resumes, and new sleeves always wait for a person, and there is no order proposal. Portfolio backtests run each sleeve through the unified model on `weight × capital` over one common window and combine them; sleeves are simulated independently and portfolio caps are not simulated.
- `thytrader-runtime portfolio-*` — deploy a portfolio: `portfolio-start --revision N` (one bot per sleeve tagged `portfolio_id`; paper cash or live allocated capital = weight × capital; planned for every sleeve, so one refusal starts nothing; live `--i-understand-live`), `portfolio-pause|resume|stop [--sleeve-id] [--flatten]`, `portfolio-reset-breaker` (always `--confirm`), and read-only `portfolio-status`. A live portfolio's sleeve allocations count as risk-policy allocation membership for its bots; portfolio caps and latched daily-loss / drawdown stops bind every sleeve with explicit reason codes (`PORTFOLIO_TOTAL_EXPOSURE_LIMIT`, `PORTFOLIO_ASSET_EXPOSURE_LIMIT`, `PORTFOLIO_BREAKER_LATCHED`, `PORTFOLIO_DAILY_LOSS_STOP`, `PORTFOLIO_DRAWDOWN_STOP`) in risk verdicts and the decision timeline ([ADR 0091](decisions/0091-portfolio-deployment-limits-and-manager-proposals.md)).
- `thytrader-playbook` — sequences existing CLIs for data → research → optional paper (`--confirm` forwarded; never live).
- `thytrader-memory` — journals, why-trade review, sentiment/pattern hooks, monitor, notify, and
  fail-closed experiential training (`--confirm`; YOLO never covers this lane).

In-app operator chat is a loopback UI (the Agent side panel on every page, or `/chat` as the full
page; [ADR 0079](decisions/0079-four-destination-shell-agent-panel-palette-tokens.md)) and
`/api/v1/operator-chat` over those same lanes ([ADR 0051](decisions/0051-in-app-operator-chat.md)). The user pastes **their** LLM API key; that
is not Coinbase. `thytrader-operator chat-status` is HTTP-only and never prints the key. Chat is
not a seventh skill lane.

Judge configured market-data coverage by `watch_complete`, not island `complete`. Catalog `sparsity` is `gapped` when the watch is incomplete. `inspect-gaps` may return `truncated=true` with a partial `gap_summary` when a server-side budget stops the scan ([ADR 0072](decisions/0072-catalog-health-bounded-gaps-self-complete-ingest.md)). `GET /api/v1/market-data/datasets` lists fingerprint-addressed island publications only. `/datasets/latest` and operator `data-catalog` are catalog-grade (structural checks, stat-identity cache, under a second warm); binding a dataset to a backtest, study, or deployment re-verifies its exact content fingerprint ([ADR 0085](decisions/0085-fast-research-ingest.md)). A CLI that prints `Timed out after N s waiting for the ThyTrader API` gave up waiting on a busy API; for a mutation, read state back before retrying.

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
`?async=true` answers 202 at once. A synchronous `POST /api/v1/backtests` or
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
`thytrader-ops-contract-v52` ([ADR 0078](decisions/0078-live-readiness-http-ack-venue-reload-definite-rejects.md),
[ADR 0082](decisions/0082-strategy-root-mutable-strategies-auto-snapshots.md),
[ADR 0083](decisions/0083-unified-backtest-model.md),
[ADR 0085](decisions/0085-fast-research-ingest.md),
[ADR 0086](decisions/0086-indicator-catalog-expansion-and-offset.md),
[ADR 0087](decisions/0087-per-bar-decision-timeline.md),
[ADR 0088](decisions/0088-portfolio-model-and-portfolio-backtest.md),
[ADR 0089](decisions/0089-agent-research-ergonomics.md),
[ADR 0090](decisions/0090-research-correctness-optional-take-profit-diagnostics.md),
[ADR 0091](decisions/0091-portfolio-deployment-limits-and-manager-proposals.md),
[ADR 0092](decisions/0092-research-worker-pool.md); `backtest_engine:
"thytrader-backtest"`; `indicator_kinds` and `indicator_offset_runtimes`;
`decision_journals: ["paper", "live"]`; `portfolio_model`; `research_dataset_autobind` and
`study_budgets`; `take_profit_kinds`, `live_protection_kinds`,
`backtest_diagnostics`, `fee_suggestion_source`; `portfolio_deployment`, `portfolio_breakers`,
`portfolio_proposal_kinds`, `portfolio_briefing_contract`; `research_worker_pool`; expected
Alembic revision `0057`).

Research correctness ([ADR 0090](decisions/0090-research-correctness-optional-take-profit-diagnostics.md)):
`exits.take_profit` may be `{"kind": "none"}` (live protects such books with a Coinbase
stop-limit); strategy responses carry advisory `validation.warnings`; `GET
/api/v1/backtests/{fp}` returns `diagnostics` (the entry funnel, outside the result
fingerprint); `GET /api/v1/backtests/{fp}/signal-trace` pages the result's entry-condition trace
(`thytrader-research-evaluate`); `GET /api/v1/fees` suggests the account's reported Coinbase
rates (`suggestion_source: coinbase_account`) with the public schedule as context; and the
decision timeline records geometry/sizing refusals as `skipped` with exact reason codes.

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

### Orchestration skill

`thytrader-playbook` sequences data → research → optional paper by calling existing CLIs. It
inherits the same Safe / YOLO confirmation rules and must not grant live authority by inheritance.

### Memory skill

`thytrader-memory` records origin-attributed journals, sentiment/pattern hooks, notify requests,
why-trade review, and trains a fail-closed advisory model from attributed local journals.
`--confirm` is always required. YOLO never covers this lane. Operator `monitor` and
`trade-reasons` are read-only. Place-order `--note` is the first why-trade note; later notes use
`add-trade-reason-note --confirm`. Training consumes `JournalEntry` as stored.

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
| Supported read-only diagnostics | `thytrader-operator`: health, configuration validity, market-data quality, strategy library state, backtest/paper/live performance slices, reconciliation, runtime watch (redacted `books[]`), persisted research-study catalog, why-trade journals, the per-bar decision timeline (`decisions`), and a redacted support bundle. Account balances and portfolio history: `GET /api/v1/portfolio` and `/history` (not an operator CLI subcommand). Deployment quantities: `thytrader-runtime show`. HTTP by default. |
| Supported strategy/backtest mutation contracts | `thytrader-research`: confirmation-gated strategy create/save/import/clone/delete, backtest submission by `strategy_id` (including `additional_instrument_datasets` for extra covered products), composed OOS / walk-forward / cross-market / sweep / WFO studies, and persisted study catalog reads. HTTP by default. |
| Paper runtime | Read-only paper-session status and fill-ledger PnL through the operator skill. Paper start/pause/resume/stop uses `thytrader-runtime` with `--confirm`. Optional `--maker-fee-rate` / `--taker-fee-rate` are documented paper assumptions ([ADR 0048](decisions/0048-paper-deploy-fee-fields.md)); omitted rates stay `0.001` / `0.002`. `thytrader-playbook` may start paper only and uses those defaults. |
| Guarded live execution | `thytrader-runtime start --mode live --confirm --i-understand-live` or, when YOLO advertises `live`, `start --mode live --i-understand-live` after an audited skip. Live `place-order` still needs `--confirm` and `--i-understand-live`. Live fills ingest through cursor-terminated List Fills and quarantine incomplete rows ([ADR 0059](decisions/0059-coinbase-list-fills-cursor-pagination.md)). Arming, cancellation of individual venue orders, configuration changes, and kill switches never inherit authority from an observation, research, or playbook skill. |
| Coinbase credentials | `thytrader-runtime show-coinbase-credentials` / `set-coinbase-credentials --private-key-file` / `clear-coinbase-credentials`. HTTP `GET/PUT/DELETE /api/v1/credentials/coinbase`. Presence flags only; GET never echoes secrets. Set/clear always `--confirm`. YOLO never covers this. Setting credentials does not arm live trading. LLM keys stay on `/chat`. |
| Experiential memory | `thytrader-memory`: confirmation-gated journals, why-trade review, sentiment/pattern hooks, notify, and fail-closed `train`. Operator `monitor` and `trade-reasons` are read-only. YOLO never covers this lane. Research `create-strategy --experiential-model-id` may merge the advisory into JSON (HTTP only). |
| In-app operator chat | Loopback `/chat` plus `/api/v1/operator-chat`. Uses the same HTTP skill routes. Mutations stay confirmation-gated; live still needs understand-live. LLM keys stay in the API process; Coinbase keys never go to the browser. |

The key principle: **agents should diagnose and explain first; trading authority is not a natural extension of observability.** Agent E2E as the primary surface ([ADR 0030](decisions/0030-agent-e2e-primary-surface.md)) does not collapse these lanes.
