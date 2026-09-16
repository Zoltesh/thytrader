# 0048: Paper deploy maker/taker fee fields

- Status: Accepted
- Date: 2026-09-16
- Relates to: [0007](0007-immutable-research-run-specifications.md),
  [0013](0013-http-first-agent-clients.md), [0019](0019-ops-contract-identity.md),
  [0030](0030-agent-e2e-primary-surface.md),
  [0031](0031-coinbase-first-platform-end-state.md)

## Context

Research already requires explicit `maker_fee_rate` / `taker_fee_rate` on immutable run
specifications and can prefill those fields from the operator's Coinbase fee-tier snapshot
mapped through `coinbase-advanced-spot-fees-v1`. Paper deployments still charged a hardcoded
`0.001` maker / `0.002` taker schedule with no deploy fields. Operators could not match paper
costs to a researched assumption without pretending those rates were live Coinbase fills.

Live venue billing is already exchange-authoritative: recorded fill fees come from Coinbase.
This slice must not invent live fee claims or add another exchange.

## Decision

Paper strategy deploy and new paper discretionary books persist Decimal maker/taker **assumptions**
on the deployment. Those rates size paper entries and charge paper fills (maker on post-only,
taker on marketable). They are documented cost assumptions, not observed Coinbase fees.

- HTTP `POST /api/v1/deployments` and `POST /api/v1/discretionary-orders` accept optional
  `maker_fee_rate` and `taker_fee_rate` on paper. Both must be present or both omitted.
  Omitted paper rates become the documented `0.001` / `0.002` defaults.
- Valid rates are finite Decimals in `[0, 0.1]`; maker must not exceed taker.
- Live start and live place-order reject the fields. Live fills keep venue-recorded fees.
- `thytrader-runtime start` / `place-order` expose `--maker-fee-rate` and `--taker-fee-rate`
  with the same rules. Playbook paper start omits the flags and therefore uses the documented
  defaults.
- The UI Deploy tab and Trade ticket expose the fields, prefill from `GET /api/v1/fees`
  suggested rates when present, otherwise the documented defaults, and label them as paper
  assumptions.
- Reusing a flat paper discretionary book keeps stored rates. A second ticket that supplies
  different rates is a conflict.
- Operator performance `fee_treatment` reports the book's actual paper rates and states they
  are not observed Coinbase fees.

### Persistence and ops contract

Alembic `0028` adds nullable `deployments.paper_maker_fee_rate` and
`deployments.paper_taker_fee_rate`, backfills existing paper rows to `0.001` / `0.002`, and
CHECKs that paper rows have both rates while live rows have neither.

Ops contract becomes `thytrader-ops-contract-v16` with `paper_deploy_fee_fields`
`maker_fee_rate` / `taker_fee_rate` and `expected_schema_revision` `0028`.

## Consequences

- Paper PnL can use the same modeled maker/taker pair as a researched run without claiming
  live Coinbase billing.
- Existing paper CLI one-liners that omit fee flags keep the documented `0.001` / `0.002`
  schedule.
- Stale Compose images that lack the columns fail the ops-contract preflight.

## Alternatives considered

- **Require paper fee fields with no defaults:** rejected; that would break existing paper
  start and playbook sequences without improving honesty of the documented schedule.
- **Point paper at the live Coinbase fee API each fill:** rejected; that would invent a live
  fee claim on a simulated book and couple paper fills to a changing venue snapshot.
- **Skip the ops-contract bump:** rejected; HTTP fields, Alembic `0028`, and CLI flags are
  content identity. Extra exchanges stay out.
