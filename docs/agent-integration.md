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

## Planned direction: agent experts that learn from evidence (hooks + V1 trainer + why-trade)

A major product goal is for agents to act as **crypto-trading experts that improve from durable
evidence** spanning market-data research, reproducible backtests, paper trades, and live trades.
Phase 14 shipped origin-attributed **hooks** ([ADR 0037](decisions/0037-phase-14-experiential-memory.md)).
Bounded V1 training ships as a fail-closed integer ranker over those attributed local journals
([ADR 0049](decisions/0049-experiential-train-v1.md)). Per-intent why-trade review ships as
`thytrader-trade-reason-v1` ([ADR 0054](decisions/0054-trade-reason-journals.md)):

- Durable journals, sentiment snapshots, and pattern observations with required `origin` (`human` or
  `agent`). Per-trade **why it was made** records freeze published strategy identity, closed-bar
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

- `thytrader-operator` — health, configuration, exchange, market-data, data-catalog, products, indicators, strategies, performance, risk, reconciliation, runtime, monitor, studies, support-bundle, schema-check, chat-status.

`uv run thytrader-operator indicators` lists the fail-closed catalog an agent may author, including
stochastic, ADX, configurable rolling inputs, and sample stdev
([ADR 0047](decisions/0047-wider-fail-closed-indicator-catalog.md)). Do not invent unlisted kinds.
- `thytrader-data` — watchlist, ingest, inspect-gaps, fill-gaps (`--confirm` on mutations; `watch-add`, `ingest`, and `fill-gaps` POSTs send installation Bearer when a token is resolvable).
- `thytrader-research` — drafts, publish, backtests, composed studies, and persisted study catalog reads (`--confirm` on mutations). Multi-instrument documents bind extra products through `additional_instrument_datasets` on submit-backtest JSON (lexicographic `product_id`; omitted when empty) ([ADR 0056](decisions/0056-multi-instrument-documents-and-pyramiding.md)). Omitting both `evaluation_start` and `evaluation_end` on `submit-backtest` fills the common LTF+HTF (and extra-clock) covered intersection. `show-result` reports the published strategy clock, including `2h` and `4h`.
- `thytrader-runtime` — paper/live start, pause, resume, stop, on-demand place-order, and write-only Coinbase credential show/set/clear (`--confirm` unless YOLO covers that tier; live also `--i-understand-live`; `--side` long or short). Default stop is managed shutdown (keep protective brackets and residual occupancy); `--flatten` / `?flatten=true` marketably exits ([ADR 0058](decisions/0058-protection-lifecycle-accounting.md)). Paper start/place-order may pass `--maker-fee-rate` / `--taker-fee-rate` (documented assumptions; omitted paper uses `0.001` / `0.002`; live rejects the flags). Credential set/clear always need `--confirm` and `--private-key-file` (never a CLI secret). YOLO never covers credentials. Setting credentials does not arm live trading. `set-risk-policy --allow-intra-strategy-pyramiding` is required for paper/live same-side adds when the published strategy also enables pyramiding ([ADR 0056](decisions/0056-multi-instrument-documents-and-pyramiding.md)). Live start and live `place-order` require a published risk policy; the compiled default cannot arm live (`LIVE_REQUIRES_PUBLISHED_POLICY`). Optional `--max-daily-loss-quote` / `--max-portfolio-exposure-quote` add absolute quote ceilings alongside the matching fraction, and optional `--max-venue-order-actions-per-minute` adds a combined entry+cancel budget that can only deny a new entry, never a cancellation or protective submission ([ADR 0063](decisions/0063-stage-5-release-discipline-ci-risk-defaults-rate-budget.md)). `list` / `show` return every product book (`positions`, `instrument_runtimes`, product-tagged orders/fills, `book_totals`). The singular `position` field is compatibility-only and always includes `product_id`; read `positions` for inventory ([ADR 0060](decisions/0060-multi-book-deployment-api.md)). `protection_status` is classified from verified attached-child coverage and venue-visible exits (`flat` / `covered` / `unprotected` / `unknown`); missing children are unprotected, not unknown ([ADR 0058](decisions/0058-protection-lifecycle-accounting.md)). Default `stop` is managed shutdown; pass `--flatten` or `?flatten=true` only to marketably exit then cancel remainders. Pause still maintains protection and does not reset breaker baselines. Operator `runtime` reports `lifecycle_command`, latches, `revision`, and `worker_lease_held`; `thytrader-runtime show` reports a `capital` block (`allocated_capital`, `venue_available_quote`, reserved/inventory/equity fields) separately from ledger `cash` ([ADR 0065](decisions/0065-deployment-capital-accounting-http.md)).
- `thytrader-playbook` — sequences existing CLIs for data → research → optional paper (`--confirm` forwarded; never live).
- `thytrader-memory` — journals, why-trade review, sentiment/pattern hooks, monitor, notify, and
  fail-closed experiential training (`--confirm`; YOLO never covers this lane).

In-app operator chat is a loopback UI (`/chat`) and `/api/v1/operator-chat` over those same lanes
([ADR 0051](decisions/0051-in-app-operator-chat.md)). The user pastes **their** LLM API key; that
is not Coinbase. `thytrader-operator chat-status` is HTTP-only and never prints the key. Chat is
not a seventh skill lane.

Judge configured market-data coverage by `watch_complete`, not island `complete`. Catalog `sparsity` is `gapped` when the watch is incomplete. `inspect-gaps` may return `truncated=true` with a partial `gap_summary` when a server-side budget stops the scan ([ADR 0072](decisions/0072-catalog-health-bounded-gaps-self-complete-ingest.md)). `GET /api/v1/market-data/datasets` lists fingerprint-addressed island publications only.

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

The earliest permitted mutation surface is limited to research artifacts:

- create or edit a strategy **draft**;
- validate and publish a new immutable strategy version;
- submit an idempotent backtest that names a published strategy and verified dataset;
- retrieve immutable results for comparison.

Each operation requires explicit user confirmation, returns stable artifact identities, and records an
audit event once audit recording exists. It may not deploy a strategy, start/stop paper execution,
arm live trading, submit/cancel Coinbase orders, modify risk limits, or perform direct storage access.

### Safe mode vs YOLO mode

**Default remains Safe mode:** mutations use `--confirm`, and live start also requires
`--i-understand-live`.

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
**`thytrader-runtime show`** / **`GET /api/v1/deployments/{id}`**. Live sizing uses the nested
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

Performance reports must state timeframe, strategy version, dataset/source, currency, fee treatment, and whether values are backtest, paper, or live.

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
4. Preserve mode, timeframe, and strategy-version context.
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
| Supported read-only diagnostics | `thytrader-operator`: health, configuration validity, market-data quality, published strategy state, backtest/paper/live performance slices, reconciliation, runtime watch (redacted `books[]`), persisted research-study catalog, why-trade journals, and a redacted support bundle. Account balances and portfolio history: `GET /api/v1/portfolio` and `/history` (not an operator CLI subcommand). Deployment quantities: `thytrader-runtime show`. HTTP by default. |
| Supported strategy/backtest mutation contracts | `thytrader-research`: confirmation-gated drafts, immutable publication, backtest submission (including `additional_instrument_datasets` for extra covered products), composed OOS / walk-forward / cross-market / sweep / WFO studies, and persisted study catalog reads. HTTP by default. |
| Paper runtime | Read-only paper-session status and fill-ledger PnL through the operator skill. Paper start/pause/resume/stop uses `thytrader-runtime` with `--confirm`. Optional `--maker-fee-rate` / `--taker-fee-rate` are documented paper assumptions ([ADR 0048](decisions/0048-paper-deploy-fee-fields.md)); omitted rates stay `0.001` / `0.002`. `thytrader-playbook` may start paper only and uses those defaults. |
| Guarded live execution | `thytrader-runtime start --mode live --confirm --i-understand-live` or, when YOLO advertises `live`, `start --mode live --i-understand-live` after an audited skip. Live `place-order` still needs `--confirm` and `--i-understand-live`. Live fills ingest through cursor-terminated List Fills and quarantine incomplete rows ([ADR 0059](decisions/0059-coinbase-list-fills-cursor-pagination.md)). Arming, cancellation of individual venue orders, configuration changes, and kill switches never inherit authority from an observation, research, or playbook skill. |
| Coinbase credentials | `thytrader-runtime show-coinbase-credentials` / `set-coinbase-credentials --private-key-file` / `clear-coinbase-credentials`. HTTP `GET/PUT/DELETE /api/v1/credentials/coinbase`. Presence flags only; GET never echoes secrets. Set/clear always `--confirm`. YOLO never covers this. Setting credentials does not arm live trading. LLM keys stay on `/chat`. |
| Experiential memory | `thytrader-memory`: confirmation-gated journals, why-trade review, sentiment/pattern hooks, notify, and fail-closed `train`. Operator `monitor` and `trade-reasons` are read-only. YOLO never covers this lane. Research `create-draft --experiential-model-id` may merge the advisory into JSON (HTTP only). |
| In-app operator chat | Loopback `/chat` plus `/api/v1/operator-chat`. Uses the same HTTP skill routes. Mutations stay confirmation-gated; live still needs understand-live. LLM keys stay in the API process; Coinbase keys never go to the browser. |

The key principle: **agents should diagnose and explain first; trading authority is not a natural extension of observability.** Agent E2E as the primary surface ([ADR 0030](decisions/0030-agent-e2e-primary-surface.md)) does not collapse these lanes.
