# ADR 0118: Clock-aware health for all watched market tails

- Status: Accepted
- Date: 2026-10-06
- Amends: [ADR 0084](0084-home-kpis-needs-attention-data-health.md)

## Context

A default-product preview does not describe an installation's watched markets. A successful
worker chunk or gap-free historical island can coexist with a stale published tail. Equally,
an hours-old daily close can be perfectly current. Operators need both clocks and coverage,
not one ambiguous green indicator.

## Decision

Add the read-only operator `data-health` report and Home data-health snapshot for every enabled
watch in the existing verified local catalog. Expected ends align to each native timeframe.
Lag measures exclusive published end versus expected closed end; timestamps are UTC. A single
missing newest close can be `settling` until the existing absolute 120-second publication grace
expires. More missing closes are stale immediately. Missing, future, or unaligned endpoints
are not green. Preserve `watch_complete`, `island_complete`, and worker status independently.

Catalog warnings or failures set `inventory_complete=false`; unknown inventory is never an
empty all-clear. This report performs no provider fetch, watch mutation, ingestion, execution,
or new automatic pause. It is published-dataset evidence, not proof of a worker decision or
venue reconciliation. Home labels the report's timestamp and exposes explicit refresh.

## Alternatives and consequences

- A fixed wall-clock age would mislabel slow clocks; rejected.
- Inferring freshness from worker success conflates chunk completion with tail currency;
  rejected.
- Probing every venue during diagnostics adds latency and changes this report's evidence
  scope; rejected. Existing per-product diagnostics remain available.
- A second local catalog read occurs when opening Home's data-health disclosure. It uses the
  catalog's existing bounded/cache-backed path, not a provider-wide query. Refresh is explicit.

## Verification

Hermetic tests cover stale successful islands, 4h/6h/daily alignment, absolute settlement,
missing/future/unaligned tails, partial inventory, GET-only API/CLI discovery, and UI wording.
