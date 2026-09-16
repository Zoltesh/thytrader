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

## Planned direction: agent experts that learn from evidence (hooks + V1 trainer)

A major product goal is for agents to act as **crypto-trading experts that improve from durable
evidence** spanning market-data research, reproducible backtests, paper trades, and live trades.
Phase 14 shipped origin-attributed **hooks** ([ADR 0037](decisions/0037-phase-14-experiential-memory.md)).
Bounded V1 training ships as a fail-closed integer ranker over those attributed local journals
([ADR 0049](decisions/0049-experiential-train-v1.md)):

- Durable journals, sentiment snapshots, and pattern observations with required `origin` (`human` or
  `agent`). Per-trade **why it was made** records (signal, strategy version, risk, discretionary
  note, fill/reconcile facts) remain destination.
- Read-only monitor of deployments, recent journals, and notification delivery.
- Config-gated user notification (`none` default, `log`, or `webhook`).
- `thytrader-memory train` (and `GET|POST /api/v1/memory/models`) never bypasses confirmation gates,
  scoped authority, auditability, or risk controls, and never substitutes for audit trails. YOLO
  never covers memory mutations. Output is advisory research input, not a live brain.

See [roadmap Phase 14](roadmap.md#phase-14-experiential-memory--hindsight--shipped) and
[bounded experiential training V1](roadmap.md#bounded-experiential-training-v1--shipped).

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
- `thytrader-data` — watchlist, ingest, inspect-gaps, fill-gaps (`--confirm` on mutations).
- `thytrader-research` — drafts, publish, backtests, composed studies, and persisted study catalog reads (`--confirm` on mutations).
- `thytrader-runtime` — paper/live start, pause, resume, stop, and on-demand place-order (`--confirm` unless YOLO covers that tier; live also `--i-understand-live`; `--side` long or short). Paper start/place-order may pass `--maker-fee-rate` / `--taker-fee-rate` (documented assumptions; omitted paper uses `0.001` / `0.002`; live rejects the flags).
- `thytrader-playbook` — sequences existing CLIs for data → research → optional paper (`--confirm` forwarded; never live).
- `thytrader-memory` — journals, sentiment/pattern hooks, monitor, notify, and fail-closed
  experiential training (`--confirm`; YOLO never covers this lane).

In-app operator chat is a loopback UI (`/chat`) and `/api/v1/operator-chat` over those same lanes
([ADR 0051](decisions/0051-in-app-operator-chat.md)). The user pastes **their** LLM API key; that
is not Coinbase. `thytrader-operator chat-status` is HTTP-only and never prints the key. Chat is
not a seventh skill lane.

Judge configured market-data coverage by `watch_complete`, not island `complete`. Catalog `sparsity` is `gapped` when the watch is incomplete. `GET /api/v1/market-data/datasets` lists fingerprint-addressed island publications only.

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
See [ADR 0034](decisions/0034-phase-12-agent-orchestration-yolo.md) and
[ADR 0043](decisions/0043-yolo-live-skip-confirm.md).

Shipped constraints:

- Opt-in configuration (`THYTRADER_YOLO_ENABLED` plus `THYTRADER_YOLO_TIERS`); never the silent
  default for observation skills.
- Scope tiers: `data`, `research`, `paper`, and `live` are independently eligible. Live YOLO
  skips `--confirm` only on live start/pause/resume/stop and never skips `--i-understand-live`.
  Live `place-order`, `set-risk-policy`, `--local` research, and memory stay confirmation-hard-gated.
  Paper YOLO never covers live.
- Audit every skipped confirmation (`confirm_skipped`) or fail closed.
- Do not collapse operator / data / research / runtime authority into one unrestricted skill.
  The playbook never starts live, including when YOLO advertises `live`.

See [agent-driven platform gap plan](plans/2026-09-12-agent-driven-platform-gap-plan.md).

### Orchestration skill

`thytrader-playbook` sequences data → research → optional paper by calling existing CLIs. It
inherits the same Safe / YOLO confirmation rules and must not grant live authority by inheritance.

### Memory skill

`thytrader-memory` records origin-attributed journals, sentiment/pattern hooks, and notify requests,
and trains a fail-closed advisory model from attributed local evidence. `--confirm` is always
required. YOLO never covers this lane. Operator `monitor` is read-only. This lane does not own
trade-reason review surfaces.

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
control, discretionary `place-order`, and risk-policy publication (`set-risk-policy --confirm`, including optional daily-loss / drawdown / rate / collar flags). `thytrader-playbook/SKILL.md` sequences those CLIs and never
starts live. `thytrader-memory/SKILL.md` documents journals, sentiment/pattern hooks, monitor,
notify, and `train` / `list-models` / `show-model` with `--confirm` (YOLO never covers that lane).
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
| Supported read-only diagnostics | `thytrader-operator`: health, configuration validity, portfolio/history freshness, market-data quality, published strategy state, backtest/paper/live performance slices, reconciliation, runtime watch, persisted research-study catalog, and a redacted support bundle. HTTP by default. |
| Supported strategy/backtest mutation contracts | `thytrader-research`: confirmation-gated drafts, immutable publication, backtest submission, composed OOS / walk-forward / cross-market / sweep / WFO studies, and persisted study catalog reads. HTTP by default. |
| Paper runtime | Read-only paper-session status and fill-ledger PnL through the operator skill. Paper start/pause/resume/stop uses `thytrader-runtime` with `--confirm`. Optional `--maker-fee-rate` / `--taker-fee-rate` are documented paper assumptions ([ADR 0048](decisions/0048-paper-deploy-fee-fields.md)); omitted rates stay `0.001` / `0.002`. `thytrader-playbook` may start paper only and uses those defaults. |
| Guarded live execution | `thytrader-runtime start --mode live --confirm --i-understand-live` or, when YOLO advertises `live`, `start --mode live --i-understand-live` after an audited skip. Live `place-order` still needs `--confirm` and `--i-understand-live`. Arming, cancellation of individual venue orders, configuration changes, and kill switches never inherit authority from an observation, research, or playbook skill. |
| Experiential memory | `thytrader-memory`: confirmation-gated journals, sentiment/pattern hooks, notify, and fail-closed `train`. Operator `monitor` is read-only. YOLO never covers this lane. Research `create-draft --experiential-model-id` may merge the advisory into JSON (HTTP only). |
| In-app operator chat | Loopback `/chat` plus `/api/v1/operator-chat`. Uses the same HTTP skill routes. Mutations stay confirmation-gated; live still needs understand-live. LLM keys stay in the API process; Coinbase keys never go to the browser. |

The key principle: **agents should diagnose and explain first; trading authority is not a natural extension of observability.** Agent E2E as the primary surface ([ADR 0030](decisions/0030-agent-e2e-primary-surface.md)) does not collapse these lanes.
