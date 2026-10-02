# ThyTrader Agent Skills

Distributable skills that operate a running ThyTrader instance through supported, versioned interfaces. They must not scrape logs, query PostgreSQL, or import private internals.

This directory is the **primary product surface** for agent-driven E2E
([ADR 0030](../docs/decisions/0030-agent-e2e-primary-surface.md)). Product of record is this
directory. Cursor auto-discovery pointers live under `.cursor/skills/` and must not diverge from
these contracts. Operating agents should open [`ops/`](../ops/README.md) so they load these skills
without the contributor GitNexus workflow. Humans and authorized agents: how to
operate is in [`docs/user/operate.md`](../docs/user/operate.md).

Lane splits and confirmation gates are unchanged: operator is read-only; data, research, and
runtime mutations require `--confirm` unless YOLO covers that tier (live also `--i-understand-live`).
YOLO is an operator-enabled opt-in (default off) that may skip `--confirm` on `data` / `research` /
`paper` / `live` after an audit. YOLO on/off and tiers live in `thytrader.yaml` and apply without
restart ([ADR 0055](../docs/decisions/0055-yaml-settings-runtime-reloadable-yolo.md)). Leftover
`THYTRADER_YOLO_TIERS=paper` is valid. Live YOLO never skips `--i-understand-live`. Live place-order,
`set-risk-policy`, `--local` research, `set-settings`, Coinbase credential set/clear, and memory stay
confirmation-hard-gated. The playbook sequences existing CLIs and never starts live. Memory
mutations always require `--confirm`; YOLO never covers that lane.

## `thytrader-operator`

Read-only diagnostics: health, redacted configuration, exchange permissions, market-data freshness, strategy/runtime status, the per-bar decision timeline (`decisions`), performance, reconciliation, and a redacted support bundle.

- Skill: [`thytrader-operator/SKILL.md`](thytrader-operator/SKILL.md)
- CLI: `uv run thytrader-operator` (HTTP by default; `--local` is explicit)
- HTTP: `GET /api/v1/operator/...`
- Account balances: `GET /api/v1/portfolio` (no operator CLI subcommand). Deployment inventory:
  `thytrader-runtime show`. See [`docs/agent/portfolio-research-ops-playbook.md`](../docs/agent/portfolio-research-ops-playbook.md).

## `thytrader-data`

Confirmation-gated watchlist, complete-only ingest jobs, and gap inspection. No paper, live, strategy, or backtest authority. Does not interpolate prices; intervals without trades are published as flat no-trade bars (ADR 0095). `POST /api/v1/data/ingest` returns 202; the market-data worker writes Parquet.

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

Confirmation-gated paper and live deployment control, risk-policy publication, YAML non-secret
settings (including YOLO), and write-only Coinbase credential show/set/clear. Read-only `list`,
`show`, and `decisions` (each bot's per-bar decision timeline) need no `--confirm`. Live start also
requires `--i-understand-live`. YOLO `live` may skip `--confirm` on start/pause/resume/stop. Live
place-order, publishing a risk policy, `set-settings`, and Coinbase credential set/clear still
require `--confirm`. YOLO never covers credentials. Setting credentials does not arm live trading.
Not an extension of operator or research.

- Skill: [`thytrader-runtime/SKILL.md`](thytrader-runtime/SKILL.md)
- CLI: `uv run thytrader-runtime`
- HTTP: `/api/v1/deployments` (incl. `/{id}/decisions`), `/api/v1/strategies/{id}/decisions`, `/api/v1/discretionary-orders`, `/api/v1/risk-policy`, `/api/v1/settings`, `/api/v1/credentials/coinbase`, and portfolio deployment `/api/v1/portfolios/{id}/start|pause|resume|stop|breaker/reset` (also per sleeve)

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
`--confirm`. Never starts live. For portfolio visibility before research, use the manual sequence in
[`docs/agent/portfolio-research-ops-playbook.md`](../docs/agent/portfolio-research-ops-playbook.md).
Not an extension of the other skills.

- Skill: [`thytrader-playbook/SKILL.md`](thytrader-playbook/SKILL.md)
- CLI: `uv run thytrader-playbook`
- HTTP: `GET /api/v1/agent-orchestration` (plus child CLI routes)

## `thytrader-memory`

Confirmation-gated journals, why-trade review, sentiment and pattern-learning hooks, monitor, user
notification, and fail-closed experiential training from attributed local journals. YOLO never skips
`--confirm`. Does not deploy, paper-trade, live-trade, arm, or cancel orders.

- Skill: [`thytrader-memory/SKILL.md`](thytrader-memory/SKILL.md)
- CLI: `uv run thytrader-memory … --confirm`
- HTTP: `/api/v1/memory`

## In-app operator chat

Loopback Agent side panel (every page) or `/chat` (full page), plus `/api/v1/operator-chat`
([ADR 0051](../docs/decisions/0051-in-app-operator-chat.md), [ADR 0079](../docs/decisions/0079-four-destination-shell-agent-panel-palette-tokens.md)).
The user pastes **their** LLM API key (not Coinbase). The chat uses the lane HTTP contracts above
with the same confirmation and understand-live gates. `uv run thytrader-operator chat-status` is
HTTP-only and never prints the key. Chat is not a seventh skill lane.

See [`docs/agent-integration.md`](../docs/agent-integration.md) for the safety model.

## Skill policy

- Read-only observation comes first.
- Do not expose secrets or raw environment values.
- Do not make direct database access part of the public agent contract.
- Distinguish backtest, paper, and live data in every report.
- State timeframe, currency, strategy version, and data completeness.
- Separate verified findings from hypotheses.
- Keep state-changing tools separate, narrowly scoped, auditable, and explicitly confirmation-gated.
- Do not silently fall back from HTTP to PostgreSQL when the API is unreachable.
