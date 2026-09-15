---
name: thytrader-memory
description: >-
  Record ThyTrader journals, sentiment and pattern-learning hooks, watch the
  monitor, and request user notifications through thytrader-memory. Use when the
  user asks to journal a fact or lesson, record sentiment, note a pattern,
  inspect monitor findings, or notify themselves. Mutations require --confirm.
  YOLO never covers this lane. Never deploys, paper-trades, live-trades, arms,
  or cancels orders. Not an extension of operator, data, research, runtime, or
  playbook skills.
---

# ThyTrader memory

Confirmation-gated experiential memory. This skill is not an extension of `thytrader-operator`,
`thytrader-data`, `thytrader-research`, `thytrader-runtime`, or `thytrader-playbook`. It does not
place orders or inherit YOLO.

HTTP-only against the loopback API (`THYTRADER_API_BASE_URL` or `http://127.0.0.1:8200`). There is
no `--local` database mode. Schema: `thytrader-experiential-memory-v1`. Monitor snapshot:
`thytrader-monitor-v1`.

Origin is required on every write: `human` or `agent`. Journals, sentiment, and pattern rows are
append-only hooks. They are not a substitute for audit trails or immutable backtest/fill evidence.
There is no model training and no venue sentiment scrape.

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

`status`, `monitor`, and `list-*` are read-only. Mutations require `--confirm`. YOLO never skips
that gate. Lessons require `--lesson-outcome` other than `none`. Evidence ids are required only
when `--evidence-kind` is not `none`.

Underlying HTTP used by this CLI:

- `GET /health/ready` (ops-contract preflight)
- `GET /api/v1/memory`
- `GET /api/v1/memory/monitor`
- `GET|POST /api/v1/memory/journals`
- `GET|POST /api/v1/memory/sentiment`
- `GET|POST /api/v1/memory/patterns`
- `GET|POST /api/v1/memory/notifications`

Operator `monitor` (`uv run thytrader-operator monitor`) is the same watch wrapped in
`thytrader-operator-report-v1`. Use that for diagnostics; use this skill to write.

## Confirmation

- Never mutate unless the user explicitly asked **and** `--confirm` is present.
- Do not retry with `--confirm` unless the user asked you to.
- Do not consult YOLO for this lane. `THYTRADER_YOLO_ENABLED` never covers journals or notify.
- Keep origin, kind, and evidence identities in any answer.

## Forbidden

- Deploying, paper/live control, arming, cancelling orders, or publishing a risk policy
- Skipping `--confirm` because YOLO is enabled
- Printing webhook URLs, API keys, private keys, `.env` values, or database URLs
- Interpolating candles or treating journals as fill-ledger origin
- Training a model or scraping external sentiment
- Direct PostgreSQL access
- Editing application source to persist memory on a running instance
- Collapsing this lane into operator, data, research, runtime, or playbook
