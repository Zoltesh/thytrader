# 0059: Cursor-terminated Coinbase List Fills with fail-closed parsing

- Status: Accepted
- Date: 2026-09-16
- Relates to: [0004](0004-safe-execution-and-access.md), [0031](0031-coinbase-first-platform-end-state.md),
  [0036](0036-phase-13-live-extras.md), [0057](0057-atomic-fill-ledger-and-product-isolation.md)

## Context

The 2026-09-16 external audit (F11 / P05 / P06) found that live fill ingest through
`CoinbaseRestBroker.list_fills` did not match the documented Advanced Trade List Fills contract
(`GET /api/v3/brokerage/orders/historical/fills`):

1. **Pagination (P05).** `GetFillsResponse` documents a `cursor` for continuation. It does not
   require `has_next`. The adapter only requested the next page when `has_next is True`, so
   cursor-only pages stopped after page 1 and a filled order could look complete with a truncated
   ledger.
2. **Units (P06).** The parser treated `size` as base quantity regardless of `size_in_quote`. A
   quote-sized fill of `1000` at price `100` became `1000` base instead of `10`.
3. **Identity and membership.** Local fill and order UUIDs were minted with `uuid7` (random bits plus
   optional observation time). Product id was ignored. Order membership was not verified.
4. **Fail-open parsing.** Malformed rows were dropped. Missing `trade_time` was replaced with local
   `utc_now()`. Missing `commission` became `0`. Incomplete financial evidence looked like a complete
   REST ledger.

Live economics arrive only through venue fill ingest ([ADR 0057](0057-atomic-fill-ledger-and-product-isolation.md)).
A truncated or mis-normalized page is therefore a quarantine condition, not a skippable warning.
This slice does not change the atomic ledger, Alembic 0035 (reserved), `OPS_CONTRACT_ID`, or the
execution loop / worker / risk gate / deployments API owned by sibling ADRs.

## Decision

1. **Cursor termination.** Continue List Fills while the response `cursor` is a non-empty string.
   An empty or missing cursor ends the stream. `has_next` is not required to continue. If a payload
   sets `has_next: true` without a cursor, raise `BrokerError`. Detect repeated cursors. Cap the
   walk at the existing 20-page limit (`limit=100` per page). Query `product_types=["SPOT"]`.
2. **Supported units.** When `size_in_quote` is `true`, convert `size / price` to base with
   `Decimal`. When it is `false` or omitted, `size` is already base. Any other `size_in_quote` value
   quarantines the page.
3. **Stable execution identity.** `venue_fill_id` is `trade_id` (fallback `entry_id`). `Fill.id` and
   the unbound `order_id` are deterministic UUID5 values derived from those venue ids so the same
   REST row re-parses to the same local identity. Reconcile still remaps `order_id` onto the persisted
   order before ingest.
4. **Membership and bounds.** Require `product_id` and `order_id` on every row; they must match the
   requested product and optional order filter. Quantity and price must be finite and strictly
   positive. `commission` must be present and a finite non-negative Decimal. `trade_time` must parse
   as timezone-aware UTC. Adjusted / non-`FILL` `trade_type` values and nonempty `future_legs` are
   not spot FILL evidence.
5. **Quarantine.** Incomplete or unparseable financial evidence raises `BrokerError`. The adapter
   must not return a partial tuple that looks like a complete ledger. Proof-token-gated pages
   (`proof_token_required: true`) are incomplete history.

## Consequences

- Cursor-only Coinbase pages and ledgers larger than 100 executions are ingested in full (until the
  page cap).
- Quote-sized fills no longer inflate base inventory.
- Operator reconciliation / live performance still reads the fill ledger; a quarantined List Fills
  call is incomplete evidence, not a healthy empty remainder. No ops-contract bump: health payload
  shape is unchanged.
- Regression fixtures live in `tests/exchanges/test_coinbase_broker.py` (cursor-only pagination,
  >100 executions, repeated cursors, quote vs base size, malformed rows, wrong product/order,
  missing timestamps, stable identity).

## Alternatives considered

- **Keep `has_next` as the only continuation flag:** rejected; it is not on `GetFillsResponse` and
  is the P05 truncation bug.
- **Drop malformed fill rows and continue:** rejected; a missing trade, wrong product, or invented
  timestamp must not look complete.
- **Treat `size` as base always:** rejected; `size_in_quote` is a documented field (P06).
- **Change reconcile/worker pause copy in this slice:** rejected; sibling ADRs own those files.
  `BrokerError` from `list_fills` already fails closed for callers.
