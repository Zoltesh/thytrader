# 0060: Multi-book deployment HTTP, operator, and UI inventory

- Status: Accepted
- Date: 2026-09-16
- Relates to: [0012](0012-operator-diagnostics.md), [0013](0013-http-first-agent-clients.md),
  [0019](0019-ops-contract-identity.md), [0030](0030-agent-e2e-primary-surface.md),
  [0054](0054-trade-reason-journals.md),
  [0056](0056-multi-instrument-documents-and-pyramiding.md)

## Context

[ADR 0056](0056-multi-instrument-documents-and-pyramiding.md) shipped multi-instrument documents:
one paper or live start still creates one deployment and one quote cash book, while the worker
evaluates covered products independently and the store already persists `positions` and
`instrument_runtimes`.

Audit finding **F29** (with note **N02**): `GET/POST /api/v1/deployments` still serialized a
single compatibility `position` and omitted product identity on orders and fills. Orders from
different products appeared under the deployment primary `product_id`. When the primary book was
flat and a secondary book was the only open inventory, `_focused_position` selected that secondary
book as `snapshot.position` **without** a product label. Agents and the Deploy UI could not tell
which Coinbase product was open.

Audit finding **F30**: multi-product why-trade records must keep the product that actually traded.
`_evaluate_lockstep_bar` already wraps each covered product in `trade_reason_scope`. Overlay
stamps `intent.product_id`, but `_record_from_submit` previously copied `deployment.product_id`
(always the primary).

This slice must not bump `OPS_CONTRACT_ID` (`thytrader-ops-contract-v21`) or Alembic (0035 is
reserved). It must not edit `execution/loop.py`, `execution_worker/service.py`,
`exchanges/coinbase_broker.py`, `risk/gate.py`, or `risk/breakers.py`. Live `--i-understand-live`
is unchanged.

## Decision

Version the **deployment HTTP models** (and the operator `DeploymentSummary` overlay) without
changing the health ops-contract identity.

### Canonical inventory

`DeploymentResponse` always includes:

- `positions[]` — every open product book, sorted by `product_id`, with Decimal strings, `side`,
  `add_count`, `protection_status` (`flat` \| `covered` \| `unprotected` \| `unknown`), and
  `compatibility_focus`.
- `instrument_runtimes[]` — one overlay row per known product (stored overlays, open books,
  working orders, plus published `covered_product_ids` so a fresh two-product start shows two
  `flat` rows before the worker seeds the store).
- `orders[]` / `fills[]` — each row carries `product_id` (blank persisted ids resolve to the
  deployment primary).
- `book_totals` — `open_books`, `working_orders`, `fill_count`. These counts must equal
  `len(positions)`, the number of `pending`/`open`/`unknown` orders, and `len(fills)`.

### Compatibility `position`

`position` remains on the JSON body as **compatibility-only**. It is the store's focused book
(primary when that book is open, otherwise the sole open book — N02) with `product_id` always
set and `compatibility_focus: true`. Clients and skills must read `positions` for inventory.
The field is not removed in this slice so older UI fixtures keep parsing.

### Protection status

Classification is HTTP/operator-facing from the snapshot. It does not submit orders.

- `flat` — no open book for that product.
- `covered` — a working stop/take-profit/bracket/time-exit for that product, or a resting
  attached child whose stop, target, and quantity still match the book.
- `unknown` — the only matching protective orders are `UNKNOWN` (unreconciled).
- `unprotected` — an open book with no venue-visible cover. Paper synthetic stops that exist
  only as fields on a filled entry are unprotected until a resting exit or attached child is
  on the snapshot.

### Operator reports

`strategies` and `runtime` `DeploymentSummary` gain `books[]`: `product_id`, `phase`, `side`
(omitted/`null` when flat), `protection_status`. No quantities, cash, or order payloads.
Schema version stays `thytrader-operator-report-v1`. `OPS_CONTRACT_ID` is unchanged.

### UI

Deploy lists every overlay and open book with product identity and protection. Orders and fills
show a Product column. `position` is labeled compatibility focus when it is the focused book.

### F30 why-trade product scope

Keep the lockstep worker wrapper as shipped on main. Fix recording only:
`product_id=resolved_product_id(intent.product_id, deployment)`. Overlay-stamped secondary
intents therefore freeze `ETH-USD` (not the BTC primary). Blank intent product ids still fall
back to the primary. No `execution_worker/service.py` rewrite.

## Consequences

Two-product deployments with different sides and quantities, including primary flat and
secondary open, are unambiguous in raw HTTP, operator `books[]`, and Deploy. Aggregate totals
are testable against the collections. Older readers that only look at `position` still see one
book, but it is now product-tagged and explicitly compatibility-only.

## Alternatives considered

- **Bump `OPS_CONTRACT_ID` because operator JSON grew `books[]`:** rejected; health already
  advertises `multi_instrument_documents`. This is additive deployment/report payload, not a
  clock, engine, or Alembic change.
- **Remove compatibility `position`:** rejected in this slice; too many UI/e2e fixtures still
  send the singular field. Document deprecation instead.
- **Rewrite the execution worker to stamp `deployment.product_id` per overlay:** rejected;
  overlay correctly leaves the parent row as the quote-cash identity. Recording reads the
  intent product instead.
- **Treat filled-entry stop/target fields as cover:** rejected; those are not venue-visible
  resting exits (paper synthetic).
