# 0024: Fail-closed agent data-loop completeness and image identity

- Status: Accepted
- Date: 2026-09-14
- Relates to: [0013](0013-http-first-agent-clients.md),
  [0016](0016-longer-complete-5m-datasets.md), [0019](0019-ops-contract-identity.md),
  [0020](0020-complete-only-15m-datasets.md), [0021](0021-complete-only-30m-datasets.md),
  [0022](0022-complete-only-6h-datasets.md), and [0023](0023-complete-only-1d-datasets.md)

## Context

A verified contiguous dataset island can be `complete: true` while covering less than a configured
watch lookback. An agent that treats island completeness or an island-only zero gap count as job
completion can stop a 90-day backfill after seeing a complete 14-day island.

Package version `0.1.0` also cannot identify the running Compose image. ADR 0019 made the operator
health path fail closed on a missing or unequal ops contract, but the data, research, and runtime
HTTP CLIs could still contact a healthy stale API and fail later with route, engine, or timeframe
errors.

## Decision

- `complete` continues to mean that the published island itself is contiguous and gap-free.
- `watch_complete` is the agent decision field for whether that complete island spans the configured
  half-open watch window `[now-lookback, now)`. Catalog, ingest status, gap inspection, and operator
  data-catalog payloads put `watch_complete` before `complete` on their serialized decision surface.
- `inspect-gaps` classifies every missing bar across the full watch window for every supported
  dataset timeframe (`1h`, `5m`, `15m`, `30m`, `6h`, and `1d`) as `not_fetched`,
  `exchange_unavailable`, or `incomplete_local`. It never interpolates. A clean island cannot turn an
  incomplete watch into `gap_count: 0`.
- Every HTTP command in `thytrader-operator`, `thytrader-data`, `thytrader-research`, and
  `thytrader-runtime` preflights `/health/ready` and requires the full ops contract to equal the
  contract compiled into the CLI. Local confirmation, live acknowledgement, document validation, and
  other no-I/O safety checks still run before the preflight. Operator HTTP fails closed before
  printing a report; it does not warn on stderr after a successful payload.
- Catalog `sparsity` is `gapped` when `watch_complete` is false, even if the published island has
  zero gaps. Dashboard ingestion copy uses the same watch decision from
  `GET /api/v1/market-data/ingestion`.
- `GET /api/v1/market-data/datasets` remains fingerprint-addressed island history. Watch completeness
  lives on catalog, ingest status, and `inspect-gaps`.
- A missing or unequal ops contract, or an agent route returning 404 while `/health/ready` returns
  200, is one stale-image signal: rebuild and restart with `make run`. The CLIs do not default-fill a
  missing contract and do not use matching package version `0.1.0` as evidence of image identity.
- Dataset reporting remains read-only in the API. The market-data worker remains the only Parquet
  publisher. Strategy and paper clocks remain `1h` and `5m`; live remains `1h`.

## Consequences

- Agents have one completion rule: proceed only when `watch_complete` is true; use `complete` only
  to describe the current island.
- Gap output can be large for a long incomplete watch. The API keeps its bounded listed-gap payload
  and reports the full `gap_count` plus `omitted_gap_count`.
- Every HTTP agent command adds one readiness preflight. A stale or unreachable API fails before the
  requested read or mutation.
- The already shipped `1d` dataset timeframe participates in the same catalog and gap rules without
  becoming a strategy, paper, or live clock.

## Alternatives considered

- Treat `complete` as watch completeness: rejected because it would silently redefine durable
  manifests and worker-state island facts established by ADR 0016.
- Infer image staleness from git history or scrape OpenAPI after a command fails: rejected because
  those are checkout diagnostics, not a stable running-instance contract.
- Compare package version only: rejected because old and current images still advertise `0.1.0`.
- Interpolate missing watch bars: rejected because it would fabricate market data and violate the
  complete-only publication contract.
