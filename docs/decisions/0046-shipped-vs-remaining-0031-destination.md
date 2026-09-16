# 0046: Shipped vs remaining Coinbase-first destination

- Status: Accepted
- Date: 2026-09-16
- Relates to: [0031](0031-coinbase-first-platform-end-state.md),
  [0033](0033-phase-10-risk-policy-registry.md),
  [0037](0037-phase-14-experiential-memory.md),
  [0038](0038-complete-only-1m-2h-4h-datasets.md),
  [0039](0039-on-demand-discretionary-trades.md),
  [0040](0040-venue-strategy-paper-live-htf-clocks.md),
  [0045](0045-spot-shorting-and-attached-entry-brackets.md)

## Context

[ADR 0031](0031-coinbase-first-platform-end-state.md) recorded the Coinbase-first platform
destination and told agents not to treat `1m`/`2h`, on-demand orders, or multi-asset deploy as
implemented. That consequence was true when 0031 was accepted. Later ADRs shipped venue datasets
and clocks, on-demand long/short with SL/TP, and concurrent single-instrument paper/live. Leaving
the 0031 consequence in place now misleads operators and agents: they treat already-shipped work as
open, or they conflate concurrent single-instrument deployments with multi-instrument strategy
documents.

This ADR does not add product scope. Extra exchanges still wait on an explicit yes. Remaining
destination (multi-instrument documents, pyramiding, a wider indicator catalog, daily-loss and
drawdown breakers, order-rate limits and collars, paper deploy fee fields, experiential ML, richer
study catalog) stays open for later slices — this ADR does not check those off.

## Decision

Restate ADR 0031 destination items as **shipped** or **still destination** so agents read current
contracts instead of 2026-09-15 slice bounds:

1. **`1m` / `2h` (and every other currently ingested venue TF, including `4h`)** are implemented as
   complete-only datasets ([ADR 0038](0038-complete-only-1m-2h-4h-datasets.md) and Phase 7) **and**
   as strategy, paper, live, discretionary, and HTF clocks
   ([ADR 0040](0040-venue-strategy-paper-live-htf-clocks.md)). Future Coinbase-listed granularities
   still need their own complete-only ADR.
2. **On-demand trades with required SL/TP** are implemented
   ([ADR 0039](0039-on-demand-discretionary-trades.md) long;
   [ADR 0045](0045-spot-shorting-and-attached-entry-brackets.md) long or short with attached entry
   brackets). Intra-strategy pyramiding is not.
3. **Multi-instrument strategy documents are not implemented.** Concurrent **single-instrument**
   paper/live under one risk policy **is** ([ADR 0033](0033-phase-10-risk-policy-registry.md)).
   Schema `max_concurrent_positions` remains `1`; one product per document.

This **supersedes** ADR 0031's consequence that agents must not treat `1m`/`2h`, on-demand orders,
or multi-asset deploy as implemented. It does **not** supersede 0031's destination itself, the
extra-exchange deferral, complete-only publication, shared strategy semantics, loopback default, or
live `--i-understand-live`.

[ADR 0038](0038-complete-only-1m-2h-4h-datasets.md)'s dataset-only clock bound is superseded in part
by [ADR 0040](0040-venue-strategy-paper-live-htf-clocks.md); this ADR only records that fact for
agents still reading 0031.

## Consequences

- Roadmap, plans, agent-integration, schema, and security docs must distinguish shipped clocks and
  on-demand from remaining multi-instrument **documents**.
- Agents may watch, research, paper, and arm `1m`/`2h` (and the rest of the ingested venue set)
  through existing skills, and may `place-order` on-demand with `--confirm` (live also
  `--i-understand-live`).
- Concurrent single-instrument under the risk-policy registry is not a multi-instrument document.
- Remaining destination listed in Context is unchanged by this ADR. Extra exchanges stay waiting.

## Alternatives considered

- **Silently rewrite ADR 0031 consequences in place:** rejected; accepted ADRs are superseded
  explicitly so the 2026-09-15 bound remains readable.
- **Check off remaining destination as shipped:** rejected; those slices are still open.
- **Treat concurrent single-instrument as multi-asset documents:** rejected; one product per
  document and `max_concurrent_positions = 1` still hold.
- **Add extra exchanges in this restatement:** rejected; Coinbase Advanced Trade spot only until
  that path is trustworthy.
