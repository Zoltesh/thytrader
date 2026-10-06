---
name: thytrader-data
description: >-
  Manage ThyTrader market-data watchlists and complete-only ingest through the
  confirmation-gated thytrader-data CLI. Use when the user asks what data exists,
  to add a product or timeframe, inspect gaps, or fill gaps. Requires --confirm
  on every mutation. Never deploys, paper-trades, live-trades, arms, or cancels
  orders. Never interpolates prices; intervals without trades are flat no-trade bars.
---

# ThyTrader data

Watchlist and complete-only historical ingest only. This skill is not an extension of
`thytrader-operator` and has no strategy, backtest, paper, live, arming, or cancellation
authority.

Default transport is the loopback HTTP API. The CLI resolves its base URL from `--base-url`, then `THYTRADER_API_BASE_URL`, then the
`THYTRADER_API_HOST` / `THYTRADER_API_PORT` settings (the same `.env` Compose reads; the default
port is `8200`, but installs may override it, so never hard-code a port). For raw `curl`, export
`THYTRADER_API_BASE_URL` and call `"$THYTRADER_API_BASE_URL/api/v1/..."`.
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

Historical candles are published only as complete Parquet ranges with manifests. Coinbase returns
no candle for an interval without trades; the worker publishes each **confirmed** one (older than the
settle window and still missing on one re-fetch) as a flat **no-trade bar**: `open = high = low =
close` = the previous close, `volume = 0`. The manifest counts them as `synthetic_no_trade_intervals`
([ADR 0095](../../docs/decisions/0095-sparse-markets-no-trade-bars-listing-floors.md)). Prices are
never interpolated, and no bar is invented before a market's first trade.

Ingest is a **job** for an **existing watch**. `POST /api/v1/data/ingest` returns **202** and sets
a watchlist flag. It never creates a watch or picks a lookback: an unwatched product/timeframe is
refused with **HTTP 409**, and the message names the `watch-add` command to run first
([ADR 0085](../../docs/decisions/0085-fast-research-ingest.md)). `fill-gaps` behaves the same. The
market-data worker (`thytrader-market-data-worker`) is the only process that writes Parquet. The
API dataset volume stays read-only. By default the CLI polls `GET /api/v1/data/ingest` until the
flag clears (after the worker covers the watch lookback) or 45 minutes elapse. Agents with bounded
execution windows should pass `--no-wait`: the mutation is identical, the CLI returns the 202-time
ingest state immediately, and the worker keeps going. Re-check progress with
`thytrader-data ingest ... --no-wait` (never re-queue to poll) or `thytrader-operator data-catalog`.

## Execution windows are not ingest jobs

[ADR 0113](../../docs/decisions/0113-deploy-anchored-window-cache.md) removes the execution
loader's single-range lifetime limit: paper/live keep a fixed deploy-anchored history using
small segmented requests, not a sliding indicator seed. Restart/eviction may require bounded
cold-cache warming before a long-lived book can evaluate again. That local warming is not a
catalog gap and does not call for `fill-gaps`, `ingest`, a larger watch, or a runtime resume.
Use `thytrader-operator data-catalog` / `market-data` to establish actual coverage before any
confirmation-gated data mutation. The newest/unsettled execution tail is always re-read and
never filled without settled, confirmed no-trade evidence; the ingest worker's own settle
window remains unchanged. Demo candles remain explicitly synthetic and now have stable UTC
identity across range segmentation, overlap, previews, and restart; they are not venue prices.

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
| Watch a long research history | `uv run thytrader-data watch-add --product-id BTC-USDC --timeframe 1d --lookback-hours 87600 --confirm` |
| Queue ingest for a watched target (CLI polls the worker) | `uv run thytrader-data ingest --product-id ETH-USD --timeframe 5m --confirm` |
| Queue ingest without polling | `uv run thytrader-data ingest --product-id ETH-USD --timeframe 5m --no-wait --confirm` |
| Classify missing bars | `uv run thytrader-data inspect-gaps --product-id ETH-USD --timeframe 5m` |
| Re-queue complete-only ingest | `uv run thytrader-data fill-gaps --product-id ETH-USD --timeframe 5m --confirm` |

`watchlist-list` and `inspect-gaps` are read-only and do not use `--confirm`. Run `watch-add`
before `ingest` for any product/timeframe that `watchlist-list` does not show; `ingest` and
`fill-gaps` on an unwatched target exit with the HTTP 409 message and change nothing.

`watch-add` checks the product against the venue's enabled spot catalog before writing. HTTP 400
`<product> is not an enabled USD or USDC spot product` is definitive (the complete catalog lacks it;
check `uv run thytrader-operator products`). HTTP 503 `Could not verify the spot product list` means
the catalog did not load or came back empty or partial (timeout, rate limit, venue error): nothing
was written, and repeating the same command is safe ([ADR 0089](../../docs/decisions/0089-agent-research-ergonomics.md)).
Research backtests and studies bind the newest complete dataset per clock automatically once it is
ingested; their HTTP 422 `datasets_missing` names the `watch-add` / `ingest` commands to run.

`watch-add` accepts USD, USDC, and USDT spot products. The web Test/Run **Download data** action uses
the same `PUT /api/v1/data/watchlist` plus no-wait `POST /api/v1/data/ingest` behind a confirmation,
and watches at the timeframe's ceiling. Optional `--lookback-hours` on `watch-add` defaults to 168
(seven days). Per-timeframe ceilings ([ADR 0085](../../docs/decisions/0085-fast-research-ingest.md)):

| Timeframe | Max `--lookback-hours` | Span | Bars at the ceiling |
|---|---|---|---|
| `1m` | 2160 | 90 days | 129,600 |
| `5m` | 8760 | 1 year | 105,120 |
| `15m` | 17520 | 2 years | 70,080 |
| `30m` | 26280 | 3 years | 52,560 |
| `1h` | 43800 | 5 years | 43,800 |
| `2h`, `4h`, `6h`, `1d` | 87600 | 10 years | 43,800 / 21,900 / 14,600 / 3,650 |

A larger value is rejected (HTTP 422) with the ceiling and its span. Coinbase often holds less
history than a ceiling allows, especially for USDC markets listed recently. The worker then proves
the listing (no candle at all back past the timeframe's ceiling) and reports it as `history_floor_at`.

How the worker walks a watch ([ADR 0085](../../docs/decisions/0085-fast-research-ingest.md)):

- Every provider request is one page of at most 350 bars on the interval grid. A one-year `1h`
  backfill is 26 requests and a one-year `1d` backfill is 2.
- Initial backfill starts at the newest closed bar and walks back toward the lookback start, so a
  dataset ending at the newest bar exists after the first request. Prefix backfill (`prefix_backfill`)
  extends an existing island back the same way. Incremental maintenance extends forward from a
  one-bar overlap.
- Each walk publishes one cumulative, fingerprint-addressed revision. UTC-day Parquet partitions and
  complete-only validation are unchanged.
- A quiet interval never shortens a series ([ADR 0095](../../docs/decisions/0095-sparse-markets-no-trade-bars-listing-floors.md)).
  A confirmed missing bar becomes a no-trade bar in both directions, and the forward walk always
  extends the same island. When the lookback start itself had no trades, the last trade before it
  prices the flat bars from the start. A missing bar newer than the settle window (one bar, at least
  15 minutes) is waited for, never filled, so a sparse `1m` head can trail closed time by up to 15
  minutes.
- `history_floor_at` is set only by a backward walk whose **listing search** found no candle at all
  before the series: pages to the UTC day boundary, then daily-candle probes back to 350 days past
  the timeframe's ceiling. Forward and incremental walks never set or move it, and an interior gap never becomes one.
  The worker re-proves a recorded floor once after each worker start. Status payloads (`ingest`,
  `fill-gaps`, catalog rows, `GET /api/v1/market-data/ingestion`) report it; when it is set, coverage
  legitimately starts at the listing.

Superseded dataset revisions are garbage-collected by the worker (bounded, audited, every 6 h). It
never deletes a fingerprint that any stored record references, or the newest revision. Operators can
run a one-shot pass with `docker compose exec market-data-worker /app/.venv/bin/thytrader-market-data-retention`
(dry run) and add `--confirm` to delete. That command is an operator repair step, not a lane
mutation; agents should report backlog rather than run it. `inspect-gaps` classifies holes across
the **watch** window, not only the current island, and never interpolates.

For a watched target, `complete` on catalog rows and on `ingest` / `fill-gaps` / `inspect-gaps`
payloads is **watch-relative**: the verified series spans the configured lookback (the same as
`watch_complete`), or starts at a proven listing floor. `island_complete` keeps the dataset-level
fact. A two-minute 1m island for a 90-day watch is not complete, and neither is a 14-day 5m island
with `lookback_hours: 8760`. Coverage is reported as `watch_covered_candle_count` of
`watch_expected_candle_count` bars (catalog rows add `watch_coverage_ratio` and
`synthetic_no_trade_intervals`). Catalog `watch_sparsity` is `gapped` while the watch is short, even
when island `sparsity` is `none`. `GET /api/v1/market-data/datasets` lists island fingerprints; it is
not the watch-completeness surface.

### Check that a series is healthy

1. `uv run thytrader-operator data-catalog` and find the row (`product_id`, `timeframe`).
2. Healthy means `watched: true`, `watch_complete: true` (`complete: true`), `worker_status:
   succeeded`, no `failure_code`, and `watch_covered_candle_count` equal to
   `watch_expected_candle_count`, unless `history_floor_at` is set. A floor is the market's listing:
   coverage then counts from it, and the data-health view shows "Complete from listing".
3. `synthetic_no_trade_intervals` above zero is normal for thin markets: those bars had no trades.
   Backtests over them disclose `synthetic_no_trade_bars`.
4. A short series with `watch_status: backfilling` is still being walked. Re-check later with
   `thytrader-data ingest --product-id ... --timeframe ... --no-wait` or `data-catalog`; do not
   re-queue to poll.
5. A short series with a `history_floor_at` well after the listing (for example a recent minute on a
   long-listed market) came from an older worker. Alembic `0059` cleared every such floor once, and
   the worker re-proves floors after each start, so it repairs itself. Report it if it persists after
   a worker restart.

`GET /api/v1/market-data/datasets/latest` and `thytrader-operator data-catalog` are catalog
listings. Each newest revision passes structural checks (manifest facts, content address, file
presence, intact Parquet files) and is served from a stat-identity cache, so warm reads stay under a
second while ingest runs. Content fingerprints are re-verified whenever a backtest, study, or
deployment binds a dataset, so a damaged revision fails closed at that point.

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

One `ingest --confirm` keeps walking until `watch_complete` or a durable failure. Every due target
gets a fair share of provider requests per worker cycle: 24 while its ingest request is pending, 8
otherwise. Covered watches are refreshed first, then pending requests (oldest first), then other
backfill. When a target runs out of budget with work left, the next cycle starts at once. Requests
are paced (0.25 s apart) and the worker touches its heartbeat before each one. A Coinbase HTTP 429
pauses every target for a shared cooldown (2 s, doubling to 60 s); a target that made no progress
shows `failure_code: provider_rate_limited` with a short retry. Wait it out; do not re-queue. Extra
`fill-gaps` calls are not needed to finish a lookback. Use `fill-gaps` only when the user asked to
re-queue continuation after a durable hole or failure.

## Confirmation

- Never run `watch-add`, `ingest`, or `fill-gaps` unless the user explicitly asked for that mutation
  **and** `--confirm` is present, unless the user explicitly asked to operate under YOLO **and**
  operator `configuration` / `thytrader-playbook status` shows the `data` tier enabled.
- If `--confirm` is missing in Safe mode, the CLI exits without writing. Do not retry with
  `--confirm` unless the user asked you to.
- Do not enable YOLO from this skill. Live YOLO is a runtime-lane setting and does not grant this skill live authority.

## Workflow

1. `uv run thytrader-operator data-catalog` and `products` to see coverage and tradable USD/USDC spot ids.
   Judge `watch_complete` and `watch_covered_candle_count` of `watch_expected_candle_count`.
2. `watch-add` (choose `--lookback-hours` up to the timeframe's ceiling) then `ingest` for a new
   product and any ingested venue clock (`1m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, or
   `1d`). `ingest` refuses an unwatched target with HTTP 409. Wait for the CLI poll or pass
   `--no-wait`; do not treat 202 as published Parquet. When a strategy uses `htf_filter` or a
   per-indicator `timeframe`, watch and ingest those extra clocks the same way before research or
   deploy. Paper and live pause on extra-TF or HTF gaps. A strategy with
   `data_requirements.reference_instruments` (ADR 0096) reads each reference `product_id` +
   `timeframe` too: a bot start is refused (409) until that series is on the enabled watchlist,
   and the refusal names the exact `watch-add … --confirm` command to run here (with the user's
   confirmation). A lagging reference does not pause bots; they skip entries as
   `reference_data_stale`.
3. `inspect-gaps` if `watch_complete` is false. Classify; do not interpolate. No-trade bars are
   published bars, not gaps. If `truncated` is true, report the partial `gap_summary` and do not
   claim the full watch was scanned.
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
- Interpolating missing candles or writing prices by hand (the worker's flat no-trade bars are the
  only synthetic bars, and only for confirmed intervals without trades)

## Catalog authority (ADR 0109)

Product reads share a bounded 30-second catalog observation whose fingerprint and
UTC timestamp appear in `thytrader-operator products`. Explicit rows override aliases.
Watch-add checks a missing listed product via authoritative direct lookup when the
provider supports it. Disabled/absent markets stay blocked; unavailable observations
fail closed rather than being called disabled. Data mutations still require --confirm.
