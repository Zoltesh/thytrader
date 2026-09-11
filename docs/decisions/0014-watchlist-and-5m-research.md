# 0014: Watchlist ingest and 5m research datasets

- Status: Accepted — superseded in part by [0015](0015-worker-owned-ingest-and-ops-workspace.md) and [0016](0016-longer-complete-5m-datasets.md)
- Date: 2026-09-10

## Context

Agents need to see which Coinbase USD products have local historical coverage, add products, fill
holes, list implemented indicators, and run 5m backtests. Paper and live execution remain a 1h
closed-bar worker. Historical candles are Parquet plus manifests, not PostgreSQL. Interpolation is
forbidden. The dedicated market-data worker is the publication path; one-shot ingest must share it.

## Decision

- Support `1h` and `5m` `CandleInterval` values on the complete-only DatasetStore path.
- Persist an operator-managed watchlist of `(provider, product_id, timeframe)` with lookback hours.
- The market-data worker iterates enabled watchlist targets and still publishes only gap-free ranges.
- Confirmation-gated `thytrader-data` talks to `/api/v1/data` on loopback. Operator stays read-only.
- Operator reports `products`, `data_catalog`, and `indicators` describe catalog, coverage, and the
  existing indicator registry (EMA, SMA, RSI, ATR, volume SMA).
- Strategy `timeframe` may be `1h` or `5m` for drafts, publication, and backtests. `create_deployment`
  rejects any timeframe other than `1h`.

## Consequences

- Seven-day 5m lookbacks originally fit a 4,032-interval cap. [0016](0016-longer-complete-5m-datasets.md)
  raises that cap and publishes complete UTC-day chunks so 5m research can cover the existing 90-day
  lookback without interpolation.
- Incomplete exchange ranges stay unpublished; `inspect-gaps` classifies `not_fetched`,
  `exchange_unavailable`, and `incomplete_local`.
- 5m paper/live is deferred until research coverage is trustworthy.
- A missing watchlist table (pre-migration) fails watch mutations closed.

## Alternatives considered

- Fold ingest into `thytrader-operator`: rejected because operator is read-only.
- Fold ingest into `thytrader-research`: rejected because research is drafts/backtests only.
- Write a second Parquet publisher for API ingest: rejected; share `ingest_once`.
  ADR 0015 keeps that shared function and makes the market-data worker its only caller.
- Enable 5m paper/live with the research timeframe: rejected until 5m research is proven.
