# 0074: Multi-book ledger and bounded deployment reads

- Status: Accepted
- Date: 2026-09-17

## Context

QA follow-up P0 items 1 and 3 found two correctness and scalability gaps before
multi-instrument paper/live use:

1. `ledger_from_snapshot()` still folded compatibility `snapshot.position` and one
   mark while deployments already carry `positions[]`, product-tagged orders, and
   per-product closes in breaker observations.
2. Deployment list and detail HTTP paths loaded every historical order and fill for
   every deployment through N× full `get_deployment()` calls.

## Decision

- Partition fill-ledger statistics by product book using product-scoped fills and a
  `marks: Mapping[str, Decimal]` argument. Aggregate equity, marked exposure, and
  deployment-level `mark_complete` across open books. Update capital refresh,
  breaker drawdown/daily-loss paths, and operator performance to consume per-product
  marks.
- Add bounded execution-store reads: paginated `list_deployments`, summary
  projections without historical orders/fills, and cursor-paginated
  `/deployments/{id}/fills` and `/orders` sub-resources. Default deployment GET uses
  `detail=summary`; `detail=full` retains the prior fully hydrated body.
- Bump ops contract to `thytrader-ops-contract-v32` with
  `bounded_deployment_reads`, `deployment_ledger_pagination`, and
  `multi_book_ledger`. Alembic marker `0045` records the contract bump. No PostgreSQL
  shape change is required.

## Consequences

- Multi-book paper/live breakers and performance reports fail closed when any open
  book lacks a disclosed last-close mark instead of silently using the primary
  product mark only.
- Deployment list and summary reads scale with open inventory and counts, not
  lifetime fill history. Clients that need every fill or order must paginate the new
  sub-resources or request `detail=full`.
- Operator and runtime CLIs expect ops contract v32 and schema revision `0045` after
  rebuild.

## References

- QA follow-up remediation plan (P0 items 1 and 3)
- ADR 0071 (USDC spot quote markets / prior contract v29)
