# 0120: Fresh accounting and verified UTC opening evidence

- Status: Accepted
- Date: 2026-10-06
- Supersedes in part: [0111](0111-durable-risk-accounting-scopes.md), trust in legacy opening
  stamps and unconditional zero-loss treatment of old flat books
- Relates to: [0058](0058-protection-lifecycle-accounting.md),
  [0106](0106-account-risk-capital-and-live-startup-baselines.md),
  [0107](0107-capital-normalized-live-performance.md)

## Context

A product overlay omitted sibling fills and could replace a complete accounting snapshot in
entry admission. A cached complete portfolio could also predate this cycle's sibling fills.
Separately, performance refresh stamped equity observed later in a UTC day as midnight equity.
Consequently, neither legacy `utc_day_open_at` nor `utc_day_open_equity` proves an opening,
even when the timestamp is exactly midnight.

## Decision

Risk admission and runtime breakers obtain fresh, unfiltered authoritative accounting for all
retained books. Product overlays explicitly declare incomplete accounting and cannot replace
that evidence. PostgreSQL reads each book's deployment, positions, orders, intents and fills in
one repeatable-read transaction. Missing reads or inconsistent accounting deny admission;
protective supervision is not dependent on successful historical price recovery.

Older books reconstruct opening cash by reversing complete, applied current-day fill cash
movements, including recorded fees. Signed quantities are replayed **separately per product**
through midnight. Current projections must match the fills, including known filled-order
quantities and, when recorded, original funding. Orphan, unapplied, future, duplicate or
contradictory evidence is unknown, not zero. A genuinely flat midnight requires no market mark.
Each nonzero overnight quantity needs its own actual closed midnight price, including inventory
closed later that day. A UTC-aligned hourly historical candle supplies that price only with exact
range boundaries and complete validated coverage. Never substitute a latest/current mark.
A book created today may still use its recorded initial funding, including exact zero.

A separate nullable `risk_day_open_evidence` records the derivation source
`per_product_applied_fills_v1`, UTC day start, exact equity, fill fingerprint and qualified product
marks. Readers reconstruct against current economics; cached equity/fingerprints are not an
admission shortcut. Qualified marks may be reused for that same UTC midnight. Stored legacy
opening fields remain unchanged and unverified. Unavailable evidence is preserved, not promoted.
An independently proven local drawdown breach can still latch when daily evidence is unknown;
a proved daily breach retains priority and quote-wide scope.

Migration **0069**, predecessor **0068**, adds only nullable evidence text. It does not backfill
or rewrite cash, fills, fees, equity, policy or latches. Downgrade refuses to erase qualified
opening evidence. Performance and latch writes use fresh runtime state; derived performance
writes are revision-checked rather than restoring stale sibling cash. Lead owns final schema
head advertisement and generated contract artifacts.

Primary operator risk reports echo the three optional entry bounds as policy configuration,
not observed balance evidence. CLI risk publication is whole-document replacement: omission
unsets an optional bound in the successor; existing historical policy documents and hashes
remain immutable. Compiled canonical bytes/defaults are unchanged.

## Alternatives and consequences

- A differently timestamped current mark still does not establish midnight equity: rejected.
- Automatically qualifying old stamps would bless historical manufactured evidence: rejected.
- A cached portfolio is not fresh merely because it is unfiltered: rejected.
- Permanent unknown despite recoverable complete fills and actual midnight prices: rejected.
- Incomplete older histories remain fail-closed until genuine evidence exists; an explicit latch
  reset cannot repair them. No new reset, funding, FX, live authority or external-trade guarantee
  is introduced. Historical recovery is read-only and bounded per overnight product.
