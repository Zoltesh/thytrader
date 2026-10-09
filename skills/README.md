# ThyTrader Agent Skills

Distributable skills that operate a running ThyTrader instance through supported, versioned
interfaces. They must not scrape logs, query PostgreSQL, or import private internals.

This directory is the **primary product surface** for agent-driven E2E
([ADR 0030](../docs/decisions/0030-agent-e2e-primary-surface.md)) and the canonical operator
documentation. Cursor pointers under `.cursor/skills/` copy each skill's frontmatter and must not
diverge; `ops/.cursor/skills/` symlinks here. Operating agents should open [`ops/`](../ops/README.md)
so they load these skills without the contributor GitNexus workflow. The human browser guide is
[`docs/user/operate.md`](../docs/user/operate.md).

## Gates (every lane)

- Operator is read-only. Data, research, runtime, portfolio, and memory mutations require
  `--confirm`; the playbook forwards it.
- YOLO is an operator-enabled opt-in (default off) that may skip `--confirm` on the `data`,
  `research`, `paper`, and `live` tiers after an audit. YOLO on/off and tiers live in
  `thytrader.yaml` and apply without restart
  ([ADR 0055](../docs/decisions/0055-yaml-settings-runtime-reloadable-yolo.md)). Leftover
  `THYTRADER_YOLO_TIERS=paper` is valid.
- Live start, live resume, and live place-order also require `--i-understand-live` (HTTP
  `i_understand_live: true`; 428 without it). YOLO never skips it.
- Always confirmation-gated, whatever YOLO says: live place-order, `set-risk-policy`,
  `set-settings`, Coinbase credential set/clear, `reset-breaker-latches`, `portfolio-reset-breaker`,
  twin link/unlink, fleet controls, `--local` research, the portfolio lane, and the memory lane.
- The playbook sequences existing CLIs and never starts live.

## Shared rules (every lane)

**Transport.** Lane CLIs talk to the loopback HTTP API. They resolve the base URL from
`--base-url`, then `THYTRADER_API_BASE_URL`, then the `THYTRADER_API_HOST` / `THYTRADER_API_PORT`
settings (the same `.env` Compose reads; the default port is `8200`, but installs may override it,
so never hard-code a port). For raw `curl`, export `THYTRADER_API_BASE_URL` and call
`"$THYTRADER_API_BASE_URL/api/v1/..."`. Operator `configuration` reports `effective_api_base_url`.
Only operator and research offer an explicit `--local`; never fall back from HTTP to PostgreSQL
when the API is down.

**Installation auth.** Production installs enforce the application trust boundary
([ADR 0061](../docs/decisions/0061-application-trust-boundary.md)): HTTP mutations need
`Authorization: Bearer <installation-token>` from `THYTRADER_INSTALLATION_TOKEN` or
`$THYTRADER_CREDENTIALS_DIR/.installation-token`. Every mutation CLI sends it automatically
([ADR 0070](../docs/decisions/0070-mutation-cli-installation-auth.md)); never paste tokens into
commands. Reads stay unauthenticated. `GET /api/v1/security/status` reports the boundary (no
secrets).

**Failures.** CLIs say what failed: `HTTP <status> <detail.code>: <detail.message>` for API
rejections, the call that timed out, the resolved origin when unreachable, or a dropped connection
(retry a read; check state before repeating a mutation). Act on the message instead of retrying
blindly.

**Stale image.** Every command preflights the full `/health/ready` ops contract. A version or
ops-contract mismatch, or HTTP 404 on an agent route while `/health/ready` is 200, means the
Compose image is stale: rebuild with `make run` (only when the user asked or the CLI reports it).
Package version `0.1.0` alone is not current-image evidence.

**Hard stop.** Operating a running instance is not a source-code task. Do not edit `src/`,
`compose.yaml`, Dockerfiles, Alembic, or tests, and do not search the tree for a patch. Report
skill/CLI failures. Open the `ops/` workspace instead of the git root, and run every
`uv run thytrader-*` command from the repository root (the parent of `ops/`).

## `thytrader-operator`

Read-only diagnostics: health, redacted configuration, exchange permissions, market-data freshness,
strategy/runtime status, the per-bar decision timeline (`decisions`), performance, readiness,
reconciliation, alerts, account balances (`portfolio`), and a redacted support bundle.

- Skill: [`thytrader-operator/SKILL.md`](thytrader-operator/SKILL.md)
- CLI: `uv run thytrader-operator` (HTTP by default; `--local` is explicit)
- HTTP: `GET /api/v1/operator/...`
- Account balances: `uv run thytrader-operator portfolio`. Deployment inventory:
  `thytrader-runtime show`. See the playbook skill's portfolio + research sequence.

## `thytrader-data`

Confirmation-gated watchlist, complete-only ingest jobs, and gap inspection. No paper, live,
strategy, or backtest authority. Does not interpolate prices; intervals without trades are
published as flat no-trade bars (ADR 0095). `POST /api/v1/data/ingest` returns 202; the market-data
worker writes Parquet.

- Skill: [`thytrader-data/SKILL.md`](thytrader-data/SKILL.md)
- CLI: `uv run thytrader-data … --confirm`
- HTTP: `/api/v1/data`

## `thytrader-research`

Confirmation-gated strategy create/save/import/clone/delete (one mutable strategy per id,
automatic snapshots at start), idempotent backtest submission by `strategy_id`, and composed
research studies (OOS / walk-forward / cross-market / parameter_sweep / walk_forward_optimization).
No paper, live, arming, or cancellation authority.

- Skill: [`thytrader-research/SKILL.md`](thytrader-research/SKILL.md)
- CLI: `uv run thytrader-research … --confirm` (HTTP by default; `--local` is explicit)

## `thytrader-runtime`

Confirmation-gated paper and live deployment control (single bots and whole portfolios), fleet
controls (`fleet-preview/status/disarm/stop/flatten/rearm`;
[ADR 0117](../docs/decisions/0117-truthful-inventory-and-fleet-controls.md)), paper/live twin
links, on-demand `place-order`, live adoption of coins already held at Coinbase
(`adoption-preview`, `place-order --entry-kind adopt`, `sell-holdings`,
`start --adopt-holdings`;
[ADR 0124](../docs/decisions/0124-inventory-adoption.md)), risk-policy publication, YAML
non-secret settings (including YOLO), and write-only Coinbase credential show/set/clear. Read-only
`list`, `show`, `decisions`, `show-twin`, `adoption-preview`, `fleet-preview`, and `fleet-status`
need no `--confirm`. YOLO `live` may skip
`--confirm` on start/pause/resume/stop only. Setting credentials does not arm live trading. Not an
extension of operator or research.

- Skill: [`thytrader-runtime/SKILL.md`](thytrader-runtime/SKILL.md)
- CLI: `uv run thytrader-runtime`
- HTTP: `/api/v1/deployments` (incl. `/{id}/decisions`, `/{id}/twin`, `/{id}/execution-quality`),
  `/api/v1/strategies/{id}/decisions`, `/api/v1/fleet-control`, `/api/v1/discretionary-orders`,
  `/api/v1/inventory-adoptions` (incl. `/preview`),
  `/api/v1/risk-policy`, `/api/v1/settings`, `/api/v1/credentials/coinbase`, and portfolio
  deployment `/api/v1/portfolios/{id}/start|pause|resume|stop|breaker/reset` (also per sleeve)

## `thytrader-portfolio`

Confirmation-gated portfolios: sleeves (one strategy each, with a capital weight), cash reserve,
shared limits, manager settings, portfolio backtests, and the append-only journal
([ADR 0088](../docs/decisions/0088-portfolio-model-and-portfolio-backtest.md)), plus the manager
loop: the read-only `deployment` and `briefing`, `propose` (rebalance, pause or resume a sleeve,
add a sleeve, with rationale and evidence), and a person's `approve` / `decline`
([ADR 0091](../docs/decisions/0091-portfolio-deployment-limits-and-manager-proposals.md)). Every
mutation needs `--confirm` and the current `revision`; YOLO never covers this lane. No deployment
or order authority: starting and stopping a portfolio are `thytrader-runtime portfolio-*`.

- Skill: [`thytrader-portfolio/SKILL.md`](thytrader-portfolio/SKILL.md)
- CLI: `uv run thytrader-portfolio … --confirm`
- HTTP: `/api/v1/portfolios` (incl. `/{id}/deployment`, `/{id}/briefing`, `/{id}/proposals`)

## `thytrader-playbook`

Sequences existing lane CLIs: data healthy → create strategy → backtest → optional paper. Forwards
`--confirm`. Never starts live. For portfolio visibility before research, use the manual sequence
in [`thytrader-playbook/SKILL.md`](thytrader-playbook/SKILL.md#portfolio--research-manual-sequence).
Not an extension of the other skills.

- Skill: [`thytrader-playbook/SKILL.md`](thytrader-playbook/SKILL.md)
- CLI: `uv run thytrader-playbook`
- HTTP: `GET /api/v1/agent-orchestration` (plus child CLI routes)

## `thytrader-memory`

Confirmation-gated journals, why-trade review, sentiment and pattern-learning hooks, monitor, user
notification, and fail-closed experiential training from attributed local journals. YOLO never
skips `--confirm`. Does not deploy, paper-trade, live-trade, arm, or cancel orders.

- Skill: [`thytrader-memory/SKILL.md`](thytrader-memory/SKILL.md)
- CLI: `uv run thytrader-memory … --confirm`
- HTTP: `/api/v1/memory`

## In-app operator chat

Loopback Agent side panel (every page) or `/chat` (full page), plus `/api/v1/operator-chat`
([ADR 0051](../docs/decisions/0051-in-app-operator-chat.md), [ADR 0079](../docs/decisions/0079-four-destination-shell-agent-panel-palette-tokens.md)).
The user pastes **their** LLM API key (not Coinbase). The chat uses the lane HTTP contracts above
with the same confirmation and understand-live gates. `uv run thytrader-operator chat-status` is
HTTP-only and never prints the key. Chat is not a skill lane and grants no extra authority.

## Skill policy

- Read-only observation comes first.
- Do not expose secrets or raw environment values.
- Do not make direct database access part of the public agent contract.
- Distinguish backtest, paper, and live data in every report.
- State timeframe, currency, strategy fingerprint, and data completeness.
- Separate verified findings from hypotheses.
- Keep state-changing tools separate, narrowly scoped, auditable, and explicitly confirmation-gated.
- Do not silently fall back from HTTP to PostgreSQL when the API is unreachable.
