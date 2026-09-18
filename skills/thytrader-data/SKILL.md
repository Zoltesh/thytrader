---
name: thytrader-data
description: >-
  Manage ThyTrader market-data watchlists and complete-only ingest through the
  confirmation-gated thytrader-data CLI. Use when the user asks what data exists,
  to add a product or timeframe, inspect gaps, or fill gaps. Requires --confirm
  on every mutation. Never deploys, paper-trades, live-trades, arms, or cancels
  orders. Never interpolates missing candles.
---

# ThyTrader data

Watchlist and complete-only historical ingest only. This skill is not an extension of
`thytrader-operator` and has no strategy, backtest, paper, live, arming, or cancellation
authority.

Default transport is the loopback HTTP API (`THYTRADER_API_BASE_URL` or `http://127.0.0.1:8200`).
There is no `--local` mode. If the API is down, stop; do not query PostgreSQL.

Production installs enforce the application trust boundary
([ADR 0061](../../docs/decisions/0061-application-trust-boundary.md)): HTTP mutations need
`Authorization: Bearer <installation-token>` from `THYTRADER_INSTALLATION_TOKEN` or
`$THYTRADER_CREDENTIALS_DIR/.installation-token` ([ADR 0070](../../docs/decisions/0070-mutation-cli-installation-auth.md)).
The CLI sends that header automatically on `watch-add`, `ingest`, and `fill-gaps` whenever a
token is resolvable; do not paste tokens into commands. Status polls (`GET /api/v1/data/ingest`)
stay unauthenticated reads.

In-app operator chat (`/chat`, `/api/v1/operator-chat`) may invoke this lane's HTTP routes. It is
not extra authority: mutations still need in-app confirmation. Do not treat chat as this skill.

Supported research, paper, and live **decision** timeframes: every ingested venue clock (`1m`,
`5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `1d`). Dataset ingest uses the same complete-only
contract. Any coarser integer-multiple venue clock may be bound as an `htf_filter`
dataset (ADR 0025, ADR 0040) or as an optional per-indicator `timeframe` (ADR 0042). Paper and live
evaluate those strategies on last-completed complete-only extra-TF and HTF bars (ADR 0041, ADR 0042).

Historical candles are published only as complete Parquet ranges with manifests. Gaps are listed
and classified, never interpolated.

Ingest is a **job**. `POST /api/v1/data/ingest` returns **202** and sets a watchlist flag. The
market-data worker (`thytrader-market-data-worker`) is the only process that writes Parquet. The
API dataset volume stays read-only. By default the CLI polls `GET /api/v1/data/ingest` until the
flag clears (after the worker finishes `ingest_once`) or 45 minutes elapse. A 90-day 5m prefix
walk can take minutes; do not treat a fast 202 as published coverage. Agents with bounded
execution windows should pass `--no-wait`: the mutation is identical, the CLI returns the 202-time
ingest state immediately, and the worker keeps going. Re-check progress with
`thytrader-data ingest ... --no-wait` (never re-queue to poll) or `thytrader-operator data-catalog`.

## Hard stop

When operating a running instance, do not edit `src/`, `compose.yaml`, Dockerfiles, Alembic, or tests.
Do not grep the tree or patch Python to make ingest writable in the API. Report failures through this
skill. Every command preflights the full `/health/ready` ops contract. Rebuild or restart only with
`make run` when the user asked, or when the CLI reports a version or ops-contract mismatch, or HTTP
404 on an agent route while `/health/ready` is 200 (the shared stale-image signal). Matching `0.1.0`
alone is not current-image evidence. Open the `ops/` workspace instead of the git root.
Run every `uv run thytrader-*` command from the repository root (the parent of `ops/`).

## Commands

| Need | Command |
|---|---|
| List the watchlist | `uv run thytrader-data watchlist-list` |
| Watch a product/timeframe | `uv run thytrader-data watch-add --product-id ETH-USD --timeframe 5m --confirm` |
| Watch disabled (no ingest until enabled) | `uv run thytrader-data watch-add --product-id ETH-USD --timeframe 5m --disabled --confirm` |
| Queue ingest (CLI polls the worker) | `uv run thytrader-data ingest --product-id ETH-USD --timeframe 5m --confirm` |
| Queue ingest without polling | `uv run thytrader-data ingest --product-id ETH-USD --timeframe 5m --no-wait --confirm` |
| Classify missing bars | `uv run thytrader-data inspect-gaps --product-id ETH-USD --timeframe 5m` |
| Re-queue complete-only ingest | `uv run thytrader-data fill-gaps --product-id ETH-USD --timeframe 5m --confirm` |

`watchlist-list` and `inspect-gaps` are read-only and do not use `--confirm`.

Optional `--lookback-hours` on `watch-add` defaults to 168 (seven days). Sub-daily clocks (`1m`,
`5m`, `15m`, `30m`, `1h`) may be set up to 2,160 hours (90 days). Slower venue clocks (`2h`,
`4h`, `6h`, `1d`) may be set up to 8,760 hours (365 days) for low-trade-count research
([ADR 0068](../../../docs/decisions/0068-slow-timeframe-watch-lookback-and-catalog-ingest.md)).
Five-minute ingest can cover a 90-day lookback (25,920 bars). One-minute ingest covers the same
lookback (129,600 bars). Fifteen-minute ingest covers the same lookback (8,640 bars).
Thirty-minute ingest covers the same lookback (4,320 bars). Two-hour ingest can cover a 365-day
lookback (4,380 bars). Four-hour ingest can cover a 365-day lookback (2,190 bars). Six-hour ingest
can cover a 365-day lookback (1,460 bars). Daily ingest can cover a 365-day lookback (365 bars).
Initial
backfill publishes complete UTC days through existing fingerprint-addressed Parquet; incomplete
days stay holes. When lookback starts before an existing complete island, the worker prepends
complete UTC-day chunks (`prefix_backfill`) and stops at the first hole. `inspect-gaps` classifies
holes across the **watch** window, not only the current island, and never interpolates.

`complete` on catalog and ingest state is **island** completeness. `watch_complete` is whether that
island spans the configured lookback. A 14-day complete 5m island with `lookback_hours: 2160` is
not done. Catalog `watch_sparsity` is `gapped` in that case while island `sparsity` may still be
`none`. `GET /api/v1/market-data/datasets` lists
island fingerprints; it is not the watch-completeness surface.

Gap `cause` values:

- `not_fetched` — this bar was never successfully published locally
- `exchange_unavailable` — a live probe did not receive that bar from Coinbase
- `incomplete_local` — an ingest attempt ran but did not publish a complete range

`fill-gaps` calls `POST /api/v1/data/fill-gaps`, which queues continuation ingest and skips the
current-island reconcile short-circuit while `watch_complete` is false. The POST uses
`request_mutation_json()` and sends installation Bearer auth the same way as `watch-add` and
`ingest` ([ADR 0070](../../docs/decisions/0070-mutation-cli-installation-auth.md)). It does not
invent prices for missing bars.

`inspect-gaps` returns `gap_summary` counts plus a capped `gaps` sample. Server-side time, probe,
and row budgets bound compute ([ADR 0072](../../docs/decisions/0072-catalog-health-bounded-gaps-self-complete-ingest.md)).
When a budget is hit the payload is fail-closed: `truncated` is true, `scanned_bar_count` names how
many bars were classified, and `gap_summary` covers only that scan. Treat `truncated` as incomplete
evidence, not as `watch_complete`. Never interpolate.

One `ingest --confirm` keeps walking until `watch_complete` or a durable failure. The worker
processes a small UTC-day budget per target per cycle, touches its heartbeat between cells and
chunks, and does not require extra `fill-gaps` calls to finish lookback. Use `fill-gaps` only when
the user asked to re-queue continuation after a durable hole or failure.

## Confirmation

- Never run `watch-add`, `ingest`, or `fill-gaps` unless the user explicitly asked for that mutation
  **and** `--confirm` is present, unless the user explicitly asked to operate under YOLO **and**
  operator `configuration` / `thytrader-playbook status` shows the `data` tier enabled.
- If `--confirm` is missing in Safe mode, the CLI exits without writing. Do not retry with
  `--confirm` unless the user asked you to.
- Do not enable YOLO from this skill. Live YOLO is a runtime-lane setting and does not grant this skill live authority.

## Workflow

1. `uv run thytrader-operator data-catalog` and `products` to see coverage and tradable USD/USDC spot ids.
   Judge `watch_complete`, not only `complete`.
2. `watch-add` then `ingest` for a new product and any ingested venue clock (`1m`, `5m`, `15m`,
   `30m`, `1h`, `2h`, `4h`, `6h`, or `1d`). Wait for the CLI poll; do not treat 202 as published
   Parquet. When a strategy uses `htf_filter` or a per-indicator `timeframe`, ingest those extra
   clocks the same way before research or deploy. Paper and live pause on extra-TF or HTF gaps.
3. `inspect-gaps` if `watch_complete` is false. Classify; do not interpolate. If `truncated` is
   true, report the partial `gap_summary` and do not claim the full watch was scanned.
4. Wait for the worker to self-complete the lookback. `fill-gaps --confirm` only if the user asked
   to re-queue after a durable hole or failure.
5. `uv run thytrader-operator indicators` before designing a study.
6. Research backtests are `skills/thytrader-research/SKILL.md`. Paper and live may use any ingested
   venue clock via `skills/thytrader-runtime/SKILL.md`. Coarser integer-multiple coverage can back an
   HTF filter or a per-indicator extra clock in research, paper, and live.

## Forbidden

- Deployments, pause/resume/stop, Coinbase orders, risk-limit edits, kill switches
- Direct PostgreSQL or Parquet writes outside this CLI
- Making the API dataset volume writable so the API process can publish Parquet
- Treating preview `GET /api/v1/market-data/preview` as a dataset
- Inventing indicators that are not in `thytrader-operator indicators`
- Interpolating missing candles
