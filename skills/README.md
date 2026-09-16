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
`paper` / `live` after an audit. Live YOLO never skips `--i-understand-live`. Live place-order,
`set-risk-policy`, `--local` research, and memory stay confirmation-hard-gated. The playbook
sequences existing CLIs and never starts live. Memory mutations always require `--confirm`; YOLO
never covers that lane.

## `thytrader-operator`

Read-only diagnostics: health, redacted configuration, exchange permissions, market-data freshness, strategy/runtime status, performance, reconciliation, and a redacted support bundle.

- Skill: [`thytrader-operator/SKILL.md`](thytrader-operator/SKILL.md)
- CLI: `uv run thytrader-operator` (HTTP by default; `--local` is explicit)
- HTTP: `GET /api/v1/operator/...`

## `thytrader-data`

Confirmation-gated watchlist, complete-only ingest jobs, and gap inspection. No paper, live, strategy, or backtest authority. Does not interpolate missing candles. `POST /api/v1/data/ingest` returns 202; the market-data worker writes Parquet.

- Skill: [`thytrader-data/SKILL.md`](thytrader-data/SKILL.md)
- CLI: `uv run thytrader-data … --confirm`
- HTTP: `/api/v1/data`

## `thytrader-research`

Confirmation-gated drafts, immutable publication, idempotent backtest submission, and composed
research studies (OOS / walk-forward / cross-market / parameter_sweep / walk_forward_optimization).
No paper, live, arming, or cancellation authority.

- Skill: [`thytrader-research/SKILL.md`](thytrader-research/SKILL.md)
- CLI: `uv run thytrader-research … --confirm` (HTTP by default; `--local` is explicit)

## `thytrader-runtime`

Confirmation-gated paper and live deployment control, plus risk-policy publication. Live start also requires `--i-understand-live`. YOLO `live` may skip `--confirm` on start/pause/resume/stop. Live place-order and publishing a risk policy still require `--confirm`. Not an extension of operator or research.

- Skill: [`thytrader-runtime/SKILL.md`](thytrader-runtime/SKILL.md)
- CLI: `uv run thytrader-runtime`
- HTTP: `/api/v1/deployments`, `/api/v1/discretionary-orders`, `/api/v1/risk-policy`

## `thytrader-playbook`

Sequences existing lane CLIs: data healthy → draft/publish → backtest → optional paper. Forwards
`--confirm`. Never starts live. Not an extension of the other skills.

- Skill: [`thytrader-playbook/SKILL.md`](thytrader-playbook/SKILL.md)
- CLI: `uv run thytrader-playbook`
- HTTP: `GET /api/v1/agent-orchestration` (plus child CLI routes)

## `thytrader-memory`

Confirmation-gated journals, sentiment and pattern-learning hooks, monitor, and user notification.
YOLO never skips `--confirm`. Does not deploy, paper-trade, live-trade, arm, or cancel orders.

- Skill: [`thytrader-memory/SKILL.md`](thytrader-memory/SKILL.md)
- CLI: `uv run thytrader-memory … --confirm`
- HTTP: `/api/v1/memory`

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
