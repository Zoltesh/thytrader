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

Supported research and paper timeframes: `1h` and `5m`. Live stays on `1h`. Dataset ingest also
supports `15m` under the same complete-only contract. Do not start 5m live. Do not treat `15m` as a
strategy, paper, or live clock.

Historical candles are published only as complete Parquet ranges with manifests. Gaps are listed
and classified, never interpolated.

Ingest is a **job**. `POST /api/v1/data/ingest` returns **202** and sets a watchlist flag. The
market-data worker (`thytrader-market-data-worker`) is the only process that writes Parquet. The
API dataset volume stays read-only. The CLI polls `GET /api/v1/data/ingest` until the flag clears
(after the worker finishes `ingest_once`) or 45 minutes elapse. A 90-day 5m prefix walk can take
minutes; do not treat a fast 202 as published coverage.

## Hard stop

When operating a running instance, do not edit `src/`, `compose.yaml`, Dockerfiles, Alembic, or tests.
Do not grep the tree or patch Python to make ingest writable in the API. Report failures through this
skill. Rebuild or restart only with `make run` when the user asked, or when HTTP 404 on `/api/v1/data`
coincides with a ready `/health/ready`, or when `thytrader-operator health` stderr reports a version
or ops-contract mismatch (stale Compose image). Open the `ops/` workspace instead of the git root.
Run every `uv run thytrader-*` command from the repository root (the parent of `ops/`).

## Commands

| Need | Command |
|---|---|
| List the watchlist | `uv run thytrader-data watchlist-list` |
| Watch a product/timeframe | `uv run thytrader-data watch-add --product-id ETH-USD --timeframe 5m --confirm` |
| Queue ingest (CLI polls the worker) | `uv run thytrader-data ingest --product-id ETH-USD --timeframe 5m --confirm` |
| Classify missing bars | `uv run thytrader-data inspect-gaps --product-id ETH-USD --timeframe 5m` |
| Re-queue complete-only ingest | `uv run thytrader-data fill-gaps --product-id ETH-USD --timeframe 5m --confirm` |

`watchlist-list` and `inspect-gaps` are read-only and do not use `--confirm`.

Optional `--lookback-hours` on `watch-add` defaults to 168 (seven days) and may be set up to
2,160 (90 days). Five-minute ingest can cover that whole lookback (25,920 bars). Fifteen-minute
ingest covers the same lookback (8,640 bars). Initial
backfill publishes complete UTC days through existing fingerprint-addressed Parquet; incomplete
days stay holes. When lookback starts before an existing complete island, the worker prepends
complete UTC-day chunks (`prefix_backfill`) and stops at the first hole. `inspect-gaps` classifies
holes across the **watch** window, not only the current island, and never interpolates.

`complete` on catalog and ingest state is **island** completeness. `watch_complete` is whether that
island spans the configured lookback. A 14-day complete 5m island with `lookback_hours: 2160` is
not done.

Gap `cause` values:

- `not_fetched` — this bar was never successfully published locally
- `exchange_unavailable` — a live probe did not receive that bar from Coinbase
- `incomplete_local` — an ingest attempt ran but did not publish a complete range

`fill-gaps` queues the same worker publication path as `ingest`. It does not invent prices for missing bars.

## Confirmation

- Never run `watch-add`, `ingest`, or `fill-gaps` unless the user explicitly asked for that mutation
  **and** `--confirm` is present.
- If `--confirm` is missing, the CLI exits without writing. Do not retry with `--confirm` unless the
  user asked you to.

## Workflow

1. `uv run thytrader-operator data-catalog` and `products` to see coverage and tradable USD spot ids.
   Judge `watch_complete`, not only `complete`.
2. `watch-add` then `ingest` for a new product, `5m`, or `15m`. Wait for the CLI poll; do not treat 202 as
   published Parquet.
3. `inspect-gaps` if `watch_complete` is false. Classify; do not interpolate.
4. `fill-gaps --confirm` to retry complete-only publication, including prefix backfill.
5. `uv run thytrader-operator indicators` before designing a study.
6. Research backtests are `skills/thytrader-research/SKILL.md`. Paper may be 1h or 5m; live stays 1h
   via `skills/thytrader-runtime/SKILL.md`.

## Forbidden

- Deployments, pause/resume/stop, Coinbase orders, risk-limit edits, kill switches
- Direct PostgreSQL or Parquet writes outside this CLI
- Making the API dataset volume writable so the API process can publish Parquet
- Treating preview `GET /api/v1/market-data/preview` as a dataset
- Inventing indicators that are not in `thytrader-operator indicators`
- Interpolating missing candles
- Starting 5m live trading
