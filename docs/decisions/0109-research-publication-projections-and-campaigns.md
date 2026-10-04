# 0109: Bounded publication evidence and reproducible research campaigns

- Status: Accepted
- Date: 2026-10-04
- Amends: [0094](0094-research-honesty-and-agent-ergonomics.md) (bounded result reads),
  [0083](0083-unified-backtest-model.md) (explicit optional execution stresses),
  [0090](0090-research-correctness-optional-take-profit-diagnostics.md) (entry economics)

## Context

The October research campaign completed its studies while individual summary reads
timed out. Summary presentation loaded full ledgers, reverified datasets and recomputed
derived metrics. Protective order replacements were also difficult to interpret, and
fee-correct simulations could reach a take-profit price without making a net profit.
Large campaigns and prospective review protocols required external orchestration.

## Decision

Bounded research reads authenticate the stored publication bytes and source-run
identity, validate their projected summary/cost/window fields, and disclose that they
are publication evidence. They do not reload Parquet or materialize trade/equity arrays.
Full reads retain artifact verification. Derived metrics are recorded outside canonical
result bytes at publication; missing legacy metrics remain null with a clear warning.
Paginated export uses the same bounded projections. Result fingerprints do not change.

Campaigns use PostgreSQL operational records and existing durable research jobs. They
pin strategy snapshots, cost/stress assumptions, windows, data bindings and review gates.
Prospective validation waits for complete data and records insufficient samples as
inconclusive. It never authorizes deployment or changes strategy rules automatically.

Fee-aware economics uses Decimal and exact, explicitly supplied assumptions. A strategy
may opt into a minimum net target-return entry guard; the same guard applies in research,
paper and live. An absent guard preserves the existing strategy semantics and identity.
It does not move a target or bypass risk checks. Strategies without targets cannot claim
a target-based economic gate passed.

Optional fill/latency stresses are deterministic assumptions inside the existing
simulator's run identity. They are disclosed sensitivity models, not observed order-book
or queue simulation. Their absence retains current semantics and canonical bytes.

Product aliases cannot override an explicit venue product row. Catalog consumers share
fresh observations; an omitted product is verified directly before it is called disabled.
Protection maintenance records describe replacements and retain links to both orders.
Historical audit records are not rewritten.

## Alternatives and consequences

- Recompute/reverify all historical artifacts on every small read: rejected because it
  couples research observation to large synchronous work and running-instance latency.
- Drop integrity checks on summaries: rejected; publication digests and source identities
  still have to match, and the verification scope is explicit.
- Infer frozen rules from a mutable strategy later: rejected; campaigns bind snapshots.
- Automatically enlarge targets or promote profitable research: rejected; declarative
  rules and runtime authority remain explicit.
- Build a full queue simulator or another job system: deferred; bounded sensitivity
  assumptions and existing worker coordination cover this slice.
