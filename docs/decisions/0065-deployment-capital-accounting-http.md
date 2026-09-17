# 0065: Deployment capital accounting on HTTP and workstation inventory

- Status: Accepted
- Date: 2026-09-17
- Relates to: [0013](0013-http-first-agent-clients.md), [0019](0019-ops-contract-identity.md),
  [0030](0030-agent-e2e-primary-surface.md), [0053](0053-workstation-ia-write-only-coinbase-credentials.md),
  [0058](0058-protection-lifecycle-accounting.md), [0060](0060-multi-book-deployment-api.md)

## Context

[ADR 0058](0058-protection-lifecycle-accounting.md) persisted live capital accounting on
``deployments`` (allocated capital, venue available quote, reserved buying power, inventory
cost, performance equity, and durable loss baselines). Operator skills and
``docs/agent-integration.md`` already told agents to read ``allocated_capital`` and
``venue_available_quote`` from ``thytrader-runtime show`` / ``GET /api/v1/deployments/{id}``,
but the HTTP body only exposed ledger ``cash``. Agents and the Deploy workstation could not
reconcile live sizing capital against venue cash or inspect inventory cost without scraping
internal stores.

Multi-book inventory from [ADR 0060](0060-multi-book-deployment-api.md) is unchanged: product
books, ``book_totals``, and ``protection_status`` stay on ``positions[]`` /
``instrument_runtimes[]``. This slice only closes the capital-accounting gap on the same
deployment payload.

## Decision

1. **HTTP ``capital`` block.** ``DeploymentResponse`` always includes ``capital`` with Decimal
   strings (or ``null`` when unset):
   - ``allocated_capital`` — risk-policy reservation for the strategy book
   - ``venue_available_quote`` — last observed venue quote; ``null`` when unknown (entries
     must fail closed)
   - ``reserved_buying_power`` — working entry quote still reserved
   - ``inventory_cost`` — entry-cost sum of every open product book
   - ``performance_equity`` — ledger equity at the last performance refresh
   - ``initial_equity``, ``baseline_equity``, ``high_water_mark_equity``,
     ``utc_day_open_equity`` — durable breaker baselines from ADR 0058

   Top-level ``cash`` remains ledger fill accounting and is never overwritten by venue quote.

2. **Ops contract.** Bump to ``thytrader-ops-contract-v24`` with
   ``expected_schema_revision`` ``0037`` and ``deployment_capital_fields`` naming the block.
   Alembic ``0037`` is a no-op marker (columns already exist from ``0035``).

3. **Workstation.** Deploy runtime inventory shows the ``capital`` block for live deployments
   (allocated vs venue vs ledger cash) without echoing secrets.

4. **Operator lane.** Operator reports continue to omit observed balances; capital stays on
   deployment HTTP / runtime ``show`` only.

## Consequences

- Agents can drive portfolio/accounting review from skills alone: venue quote unknown means
  ``capital.venue_available_quote`` is ``null``, not a silent reuse of ``cash``.
- Stale Compose images missing the block fail the ops-contract preflight.
- No change to lifecycle commands, attached-child protection, or multi-book inventory shape.

## Alternatives considered

- **Document that capital is Postgres-only:** rejected; skills already promised HTTP and broke
  agent E2E runs.
- **Overload ``cash`` for live venue quote:** rejected; ADR 0058 explicitly separates ledger
  cash from venue observation.
- **Bump only schema revision without ``deployment_capital_fields``:** rejected; health must
  advertise the new block for stale-image detection.
