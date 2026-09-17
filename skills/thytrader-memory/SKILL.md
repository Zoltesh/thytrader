---
name: thytrader-memory
description: >-
  Record ThyTrader journals, why-trade review notes, sentiment and pattern-learning
  hooks, watch the monitor, request user notifications, and train a fail-closed
  experiential model from attributed local journal evidence through thytrader-memory.
  Use when the user asks to journal a fact or lesson, review why a trade was made,
  record sentiment, note a pattern, inspect monitor findings, notify themselves, or
  train/list/show an experiential model. Mutations require --confirm. YOLO never
  covers this lane. Never deploys, paper-trades, live-trades, arms, or cancels
  orders. Not an extension of operator, data, research, runtime, or playbook skills.
---

# ThyTrader memory

Confirmation-gated experiential memory. This skill is not an extension of `thytrader-operator`,
`thytrader-data`, `thytrader-research`, `thytrader-runtime`, or `thytrader-playbook`. It does not
place orders or inherit YOLO.

HTTP-only against the loopback API (`THYTRADER_API_BASE_URL` or `http://127.0.0.1:8200`). There is
no `--local` database mode.

Production installs enforce the application trust boundary
([ADR 0061](../../docs/decisions/0061-application-trust-boundary.md)): HTTP mutations need
`Authorization: Bearer <installation-token>` from `THYTRADER_INSTALLATION_TOKEN` or
`$THYTRADER_CREDENTIALS_DIR/.installation-token` ([ADR 0070](../../docs/decisions/0070-mutation-cli-installation-auth.md)).
The CLI sends that header automatically. YOLO never covers this lane.

Schema: `thytrader-experiential-memory-v1`. Monitor snapshot:
`thytrader-monitor-v1`. Trained models: `thytrader-experiential-model-v1` from engine
`thytrader-experiential-train-v1`. Advisory: `thytrader-experiential-advisory-v1`.

In-app operator chat (`/chat`, `/api/v1/operator-chat`) may invoke these same HTTP routes, including
`GET|POST /api/v1/memory/models`. Memory mutations including `train` always need in-app
confirmation; YOLO never covers this lane. Do not treat chat as this skill.

Origin is required on every write: `human` or `agent`. Journals, sentiment, and pattern rows are
append-only hooks. They are not a substitute for audit trails or immutable backtest/fill evidence.
`train` consumes attributed local journals as stored. It does not invent journal kinds.
Why-trade review is this lane (`thytrader-trade-reason-v1`). There is no venue sentiment scrape.

Notify default is `THYTRADER_NOTIFY_PROVIDER=none` (persist as skipped, send nothing). `log` writes
a structured line. `webhook` POSTs JSON to `THYTRADER_NOTIFY_WEBHOOK_URL`. Never print that URL.

## Hard stop

When operating a running instance, do not edit `src/`, `compose.yaml`, Dockerfiles, Alembic, or tests.
Do not search the tree for a code patch. Report failures through this skill. Every command preflights
the full `/health/ready` ops contract. Rebuild or restart only with `make run` when the user asked,
or when the CLI reports a version or ops-contract mismatch, or HTTP 404 on an agent route while
`/health/ready` is 200 (the shared stale-image signal). Matching `0.1.0` alone is not current-image
evidence. Open the `ops/` workspace instead of the git root. Run every
`uv run thytrader-*` command from the repository root (the parent of `ops/`).

## Commands

| Need | Command |
|---|---|
| Counts and redacted notifier flags | `uv run thytrader-memory status` |
| Watch deployments + recent journals | `uv run thytrader-memory monitor` |
| List journals | `uv run thytrader-memory list-journals [--origin human\|agent] [--kind fact\|lesson\|note]` |
| Append a journal | `uv run thytrader-memory add-journal --origin human --kind note --title "…" --body "…" --confirm` |
| List sentiment | `uv run thytrader-memory list-sentiment [--origin human\|agent]` |
| Append sentiment | `uv run thytrader-memory add-sentiment --origin agent --label bullish --confirm` |
| List pattern hooks | `uv run thytrader-memory list-patterns [--origin human\|agent] [--pattern-key foo]` |
| Append a pattern hook | `uv run thytrader-memory add-pattern --origin human --pattern-key morning_gap --name "…" --hypothesis "…" --confirm` |
| List notify attempts | `uv run thytrader-memory list-notifications` |
| Request a notification | `uv run thytrader-memory notify --origin human --title "…" --body "…" --confirm` |
| Train from attributed local evidence | `uv run thytrader-memory train --origin agent [--seed 1] --confirm` |
| List trained models | `uv run thytrader-memory list-models` |
| Show one trained model | `uv run thytrader-memory show-model --model-id UUID` |
| List why-trade records | `uv run thytrader-memory list-trade-reasons [--origin human\|agent\|runtime] [--deployment-id UUID] [--intent-id UUID]` |
| Show one why-trade record | `uv run thytrader-memory show-trade-reason --intent-id UUID` |
| Append a why-trade note | `uv run thytrader-memory add-trade-reason-note --intent-id UUID --origin human --body "…" --confirm` |

`status`, `monitor`, `list-*`, `show-model`, and `show-trade-reason` are read-only. Mutations
require `--confirm`. YOLO never skips that gate. Lessons require `--lesson-outcome` other than
`none`. Evidence ids are required only when `--evidence-kind` is not `none`. `train` is
fail-closed: unevidenced rows are skipped; dangling local evidence refuses the whole train.
Output is advisory, not a live policy. Place-order `--note` is the first why-trade note; later
notes use `add-trade-reason-note --confirm`. Pass a model id into research with
`uv run thytrader-research create-draft --experiential-model-id UUID --confirm` (HTTP only).

Underlying HTTP used by this CLI:

- `GET /health/ready` (ops-contract preflight)
- `GET /api/v1/memory`
- `GET /api/v1/memory/monitor`
- `GET|POST /api/v1/memory/journals`
- `GET|POST /api/v1/memory/sentiment`
- `GET|POST /api/v1/memory/patterns`
- `GET|POST /api/v1/memory/notifications`
- `GET|POST /api/v1/memory/models`
- `GET /api/v1/memory/models/{id}`
- `GET /api/v1/memory/trade-reasons`
- `GET /api/v1/memory/trade-reasons/{intent_id}`
- `POST /api/v1/memory/trade-reasons/{intent_id}/notes`

Operator `monitor` (`uv run thytrader-operator monitor`) is the same watch wrapped in
`thytrader-operator-report-v1`. Operator `trade-reasons` is the same why-trade payload wrapped for
diagnostics. Use that for diagnostics; use this skill to write notes.

## Confirmation

- Never mutate unless the user explicitly asked **and** `--confirm` is present.
- Do not retry with `--confirm` unless the user asked you to.
- Do not consult YOLO for this lane. `THYTRADER_YOLO_ENABLED` never covers journals, notify, train, or why-trade notes.
- Keep origin, kind, evidence identities, and model fingerprints in any answer.

## Forbidden

- Deploying, paper/live control, arming, cancelling orders, or publishing a risk policy
- Skipping `--confirm` because YOLO is enabled
- Printing webhook URLs, API keys, private keys, `.env` values, or database URLs
- Interpolating candles or treating journals as fill-ledger origin
- Treating a trained model as a live brain, order intent, or Coinbase call
- Scraping external sentiment
- Interpolating candles or rewriting fill-ledger origin from why-trade notes
- Direct PostgreSQL access
- Editing application source to persist memory on a running instance
- Collapsing this lane into operator, data, research, runtime, or playbook
