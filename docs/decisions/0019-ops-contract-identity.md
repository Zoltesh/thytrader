# 0019: Ops-contract identity independent of package version

- Status: Accepted
- Date: 2026-09-11
- Relates to: [0012](0012-operator-diagnostics.md), [0013](0013-http-first-agent-clients.md),
  [0016](0016-longer-complete-5m-datasets.md), [0017](0017-maker-limit-bar-backtest.md),
  [0018](0018-5m-paper-not-live.md), [0020](0020-complete-only-15m-datasets.md)

## Context

ThyTrader's package `__version__` is still `0.1.0`. A Compose image built before 5m paper,
`thytrader-bar-backtest-v3`, or the 25,920-bar cap served the same version string as HEAD CLI.
Operator health stayed `healthy`. Agents correctly refused `make run` because HTTP was ready.
Research then got HTTP 422 on v3 and paper got HTTP 409 on 5m — the running image was stale, not
the HEAD source.

Default-filling a missing health `ops_contract` would hide that mismatch.

## Decision

Health reports and `/health/live` / `/health/ready` advertise an ops contract:

- `id` (`OPS_CONTRACT_ID`, currently `thytrader-ops-contract-v3`)
- `max_historical_interval_count`
- `backtest_engines` (v1, v2, v3)
- `paper_timeframes` (`1h`, `5m`)
- `live_timeframes` (`1h`)
- `expected_schema_revision` (`0017`)

A missing payload is a mismatch. The operator CLI writes the same rebuild hint used for version
mismatch: rebuild with `make run`. Bump `OPS_CONTRACT_ID` whenever those facts
change. Do not treat matching `0.1.0` as proof the running image matches this CLI.

## Consequences

- An ops agent can tell a healthy old `0.1.0` image from this checkout without scraping OpenAPI.
- Skills and `ops/` treat version mismatch, ops-contract mismatch, and 404-on-ready as stale image.
- Schema revision `0017` remaining on the database while HEAD expects it is not by itself a content
  identity; the contract still lists it so a skipped migration is visible when present.

## Alternatives considered

- Bump `__version__` on every merge: rejected as the long-term signal, but it would not have helped
  images already labeled `0.1.0` until every rebuild remembered to bump it.
- Put git commit or image digest in health: useful later; not required to distinguish this feature
  set, and Compose rebuilds already change the image.
- Default-factory-fill a missing `ops_contract` when parsing old JSON: rejected; that would mark a
  stale API as current.
