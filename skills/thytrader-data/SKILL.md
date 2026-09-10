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

Supported research timeframes: `1h` and `5m`. Paper and live stay on `1h`.

Historical candles are published only as complete Parquet ranges with manifests. Gaps are listed
and classified, never interpolated.

## Commands

| Need | Command |
|---|---|
| List the watchlist | `uv run thytrader-data watchlist-list` |
| Watch a product/timeframe | `uv run thytrader-data watch-add --product-id ETH-USD --timeframe 5m --confirm` |
| Ingest one complete range | `uv run thytrader-data ingest --product-id ETH-USD --timeframe 5m --confirm` |
| Classify missing bars | `uv run thytrader-data inspect-gaps --product-id ETH-USD --timeframe 5m` |
| Re-run complete-only ingest | `uv run thytrader-data fill-gaps --product-id ETH-USD --timeframe 5m --confirm` |

`watchlist-list` and `inspect-gaps` are read-only and do not use `--confirm`.

Optional `--lookback-hours` on `watch-add` defaults to 168 (seven days). Five-minute ingest is
capped at the same complete-range limit as Coinbase (4,032 bars, 14 days of 5m).

Gap `cause` values:

- `not_fetched` — this bar was never successfully published locally
- `exchange_unavailable` — a live probe did not receive that bar from Coinbase
- `incomplete_local` — an ingest attempt ran but did not publish a complete range

`fill-gaps` is the same publication path as `ingest`. It does not invent prices for missing bars.

## Confirmation

- Never run `watch-add`, `ingest`, or `fill-gaps` unless the user explicitly asked for that mutation
  **and** `--confirm` is present.
- If `--confirm` is missing, the CLI exits without writing. Do not retry with `--confirm` unless the
  user asked you to.

## Workflow

1. `uv run thytrader-operator data-catalog` and `products` to see coverage and tradable USD spot ids.
2. `watch-add` then `ingest` for a new product or `5m`.
3. `inspect-gaps` if coverage is incomplete. Classify; do not interpolate.
4. `fill-gaps --confirm` to retry complete-only publication.
5. `uv run thytrader-operator indicators` before designing a study.
6. Research backtests are `skills/thytrader-research/SKILL.md`. Paper/live remain 1h-only via
   `skills/thytrader-runtime/SKILL.md`.

## Forbidden

- Deployments, pause/resume/stop, Coinbase orders, risk-limit edits, kill switches
- Direct PostgreSQL or Parquet writes outside this CLI
- Treating preview `GET /api/v1/market-data/preview` as a dataset
- Inventing indicators that are not in `thytrader-operator indicators`
- Arming 5m paper or live trading
