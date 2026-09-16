# 0053: Workstation IA and write-only Coinbase credentials

- Status: Accepted
- Date: 2026-09-16
- Relates to: [0001](0001-sveltekit-frontend.md),
  [0006](0006-credential-permission-acceptance.md),
  [0030](0030-agent-e2e-primary-surface.md),
  [0031](0031-coinbase-first-platform-end-state.md),
  [0046](0046-shipped-vs-remaining-0031-destination.md),
  [0051](0051-in-app-operator-chat.md),
  [0052](0052-richer-sweep-axes-study-catalog.md),
  [0054](0054-trade-reason-journals.md),
  [0055](0055-yaml-settings-runtime-reloadable-yolo.md)

## Context

The SvelteKit workstation already shipped strategy create, backtests, paper/live, trade, and
in-app operator chat ([ADR 0051](0051-in-app-operator-chat.md)), but research and deploy still
lived inside a crowded strategy-library drawer. Destination still asked for uncluttered
professional surfaces and a loopback form to set, rotate, and clear Coinbase Advanced Trade
secrets without echoing them ([docs/roadmap.md](../roadmap.md) Workstation IA and Coinbase
secrets UI rows).

Agent-driven E2E remains the primary product surface ([ADR 0030](0030-agent-e2e-primary-surface.md)).
A UI must not replace confirmation-gated skills. Coinbase keys stay server-side
([ADR 0006](0006-credential-permission-acceptance.md)). Extra exchanges stay out.

YAML file settings and runtime-reloadable YOLO tiers now live on `/settings` as shipped by
[ADR 0055](0055-yaml-settings-runtime-reloadable-yolo.md). This slice does not reimplement that
panel. It places write-only Coinbase credentials beside it.

This slice does not rewrite [ADR 0047](0047-wider-fail-closed-indicator-catalog.md) (indicator
catalog), [ADR 0048](0048-paper-deploy-fee-fields.md) (paper deploy fees),
[ADR 0049](0049-experiential-train-v1.md) (experiential trainer),
[ADR 0050](0050-daily-loss-drawdown-rate-collars.md) (breakers),
[ADR 0051](0051-in-app-operator-chat.md) (`/chat` and operator-chat HTTP),
[ADR 0052](0052-richer-sweep-axes-study-catalog.md) (richer sweep axes and persisted study catalog),
or [ADR 0054](0054-trade-reason-journals.md) (why-trade review on Memory and Trade; ops contract
`thytrader-ops-contract-v20`, Alembic `0032`). It does not rewrite
[ADR 0055](0055-yaml-settings-runtime-reloadable-yolo.md) (YAML non-secret settings and YOLO).
It does not bump the ops contract or add Alembic revisions.

## Decision

Keep SvelteKit as the workstation UI ([ADR 0001](0001-sveltekit-frontend.md)). Give research,
paper and live deploy, and experiential journals first-class routes. Keep the Chat nav slot and
`/chat` as shipped by ADR 0051 — this slice does not reimplement chat. Keep why-trade review on
Memory and Trade as shipped by ADR 0054 — this slice does not reimplement `TradeReasonReview` or
add a third why-trade dump. Settings keeps the ADR 0055 YAML/YOLO panel and adds a write-only
Coinbase secrets section (`CoinbaseCredentialsPanel`) beside it. The strategy library drawer keeps
Insight and Versions only; Research and Deploy become links. The paper/live cell opens
`/deploy`.

Write-only Coinbase credentials:

- `GET/PUT/DELETE /api/v1/credentials/coinbase` report presence flags only. GET never returns
  secrets. PUT/DELETE never echo request bodies in 422 payloads.
- Persist to the dotenv file when writable (`0o600`). Always hot-reload this API process.
  Workers still require a restart. Setting credentials does not arm live trading.
- `thytrader-runtime show-coinbase-credentials`, `set-coinbase-credentials --private-key-file`,
  and `clear-coinbase-credentials`. Mutations require `--confirm`. YOLO never covers them.
- The Settings form wipes fields before the HTTP request. LLM keys stay on `/chat`
  ([ADR 0051](0051-in-app-operator-chat.md)). Extra exchanges are out of scope.

Paper deploy on `/deploy` keeps ADR 0048 maker/taker assumption fields. Research on `/research`
keeps ADR 0052 richer sweep-axis targets.

## Consequences

- Operators can find research and deploy without opening a crowded inspector.
- Agents set Coinbase secrets through the runtime skill without scraping `.env` or logs.
- Compose workers still interpolate host `.env` at start; the API reports
  `workers_require_restart`.
- `/chat` remains ADR 0051. The study catalog remains ADR 0052. Why-trade review remains
  ADR 0054 on Memory and Trade. YAML settings and YOLO remain ADR 0055 on the same Settings page.
  Extra exchanges remain parked.

## Alternatives considered

- **UI-only credential storage without HTTP/CLI:** rejected; agents could not drive the surface
  from skills, contradicting ADR 0030.
- **Echo key names on GET for operator convenience:** rejected; that puts secrets in browser
  payloads.
- **Bump the ops contract for the credentials route:** rejected; the route is additive presence
  flags plus dotenv persistence, not paper/live timeframe, engine, schema, or study-catalog
  identity.
- **Reimplement `/chat` in this slice:** rejected; ADR 0051 already shipped operator chat.
- **Reimplement why-trade review on `/journals`:** rejected; ADR 0054 already ships Memory/Trade
  review and forbids a third nav dump.
- **Reimplement YAML/YOLO on Settings:** rejected; ADR 0055 already ships that panel. This slice
  places Coinbase credentials beside it.
- **Additional exchanges in Settings:** rejected; Coinbase-first until that path is trustworthy.
