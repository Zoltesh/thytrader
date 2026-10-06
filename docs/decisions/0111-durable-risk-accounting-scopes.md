# 0111: Durable risk-accounting scopes

- Status: Accepted; UTC opening provenance and unconditional flat-book zero fallback
  superseded by [0120](0120-verified-risk-opening-evidence.md)
- Date: 2026-10-06
- Supersedes in part: [0050](0050-daily-loss-drawdown-rate-collars.md), daily-loss occupancy,
  quote and drawdown scope; [0082](0082-strategy-root-mutable-strategies-auto-snapshots.md),
  deletion of paper execution evidence only
- Relates to: [0106](0106-account-risk-capital-and-live-startup-baselines.md),
  [0107](0107-capital-normalized-live-performance.md),
  [0064](0064-deployment-http-lifecycle-and-breaker-latch-reset.md)

## Context

Stopping a flat deployment removed its daily loss and daily-loss latch from subsequent risk
checks. Replacement deployments could enter without that evidence. Any drawdown latch blocked
unrelated strategies across the mode. Paper strategy deletion destroyed the evidence entirely.
A first discretionary entry denied before book creation could also fail to leave a durable latch.

Account capital from risk-bearing books ([ADR 0106](0106-account-risk-capital-and-live-startup-baselines.md))
is not the evidence set for the day's loss. Pinned performance capital
([ADR 0107](0107-capital-normalized-live-performance.md)) is unchanged.

## Decision

### Occupancy, quote scope, and evidence

Exposure, capital, position slots, and order rates retain risk-bearing occupancy: running,
paused, and stopped residual books. A stopped flat book does not occupy capital or a rate slot.
Every working entry remainder counts as exposure even if its runtime overlay still says FLAT.
The cost-basis capital formula remains unchanged; no bot allocation becomes account capital.

Daily loss and its latch include **all retained running, paused, and stopped deployments** in
that mode. Loss, exposure, held-quote capital, and optional reserve calculations select the
proposed spot quote. USD, USDC, and USDT are never added or converted. Unsupported or mixed-quote
shared-cash books deny with `BREAKER_MARK_MISSING`. A shared portfolio cannot compare exposure
across quotes without FX evidence (`PORTFOLIO_LIMITS_UNAVAILABLE`). Paper deployment admission
refuses mixed-quote starting-cash comparisons instead of summing different currencies.
The published `paper_capital_quote` is one funding envelope, not a map of independently funded
quote accounts. Occupied foreign-quote books cannot simply be omitted to grant each quote the
whole envelope again. Concurrent multi-quote paper funding needs explicit per-quote budgets or
an approved valuation policy; neither is implied by this slice. Retained stopped **flat** evidence
is not a funding commitment and does not trigger that refusal.

### UTC-day correctness

Daily PnL is current ledger equity minus recorded opening equity when `utc_day_open_at` belongs
to the observation's UTC day. A deployment created on that day can use recorded opening equity;
exact zero remains valid. No baseline, ledger cash, or historical fee is rewritten.

For an older **flat** book lacking a current-day baseline:

- No current-day fills contributes zero, not yesterday's loss.
- Otherwise replay signed base quantities **per product**. Midnight and current inventory must
  both be flat; day cash movements minus recorded day fees then equal day equity change.
- A closure of overnight inventory without opening marks is unknown, not lifetime realized PnL
  attributed to today. Orphan fills or a contradictory flat projection are likewise unknown.
- An open book without a current-day baseline is incomplete. No midnight mark is invented.

Unapplied live fills make operational cash incomplete even with a same-day baseline and deny new
risk until economics reconcile. This includes legacy unmarked live fill rows: missing projection
is not assumed applied. An unknown/nonpositive live account denominator also denies.

### Drawdown and reset

Drawdown latch/breach applies to the same strategy identity (including its retained deployment
history), or to an actual discretionary book on the same product and quote. An unrelated
strategy on that product is not affected. A deleted strategy book with a null strategy FK is
**not** a discretionary book. Pinned-capital and durable-peak drawdown math is unchanged.

Daily loss pauses only running books in its mode and quote. Preserve deliberate pauses and
stopped status. Strategy trips latch the source row. Discretionary denial before candidate
creation keeps one latch on a persisted same-quote peer if none exists; it never tries to save
an unpersisted candidate or latches every historical book. Protection/exits remain ungated.

Stop, deletion, replacement, restart, and UTC rollover do not clear latches.
`reset-breaker-latches` explicitly clears flags on the named deployment carrying the latch,
without erasing same-day loss, peak, or pinned capital and without resuming it. Continuing loss
can trip again. Existing reset HTTP/CLI confirmation and audit behavior remains in force.

### Deletion and storage

Strategy deletion still refuses running/paused books and removes research/root data, but retains
**stopped paper and live deployments, ledgers, and referenced snapshots**, detaching via the
existing strategy FK. No account-day accumulator is necessary when evidence is retained.
The existing deletion count `paper_deployments` counts removals and is now zero; it does not
advertise retained rows as deleted. `live_deployments_kept` keeps its existing meaning.

Alembic **`risk0065`** (reserved lane revision; predecessor `0064`, lead rechains for release)
widenes only `ck_deployments_kind_identity` to permit detached stopped paper books. No financial
rows are rewritten. Downgrade refuses while detached paper books exist. Schema migration must
precede the new deletion behavior; coordinated API/worker release is necessary so old API
writers do not continue deleting paper evidence. Lead owns the shared ops-contract/schema-head
advertisement. This slice does not change those global constants.

### Optional entry bounds

`max_order_quantity`, `max_order_notional_quote`, and `min_available_quote_reserve` are absent
from canonical policy bytes when unset. Compiled defaults, existing published documents, and
historical fingerprints are unchanged. Configured caps deny only risk-increasing entry admission
(`MAX_ORDER_QUANTITY`, `MAX_ORDER_NOTIONAL`, `BALANCE_RESERVE`); reductions bypass them.
New monetary bounds require the proposed product quote to equal the policy's `quote_currency`.

Reserve is **notional admission headroom, not a guaranteed settled/post-fill account balance**.
Live uses observed available quote minus the candidate notional and local unheld buy remainders.
Confirmed venue holds already excluded from available quote are not subtracted twice; pending
or unknown buy holds deny. Unknown live fees and slippage are not invented or guaranteed covered.
Paper uses its capital envelope plus occupied-book cash changes, minus working buys and the
candidate with conservative stored/default paper taker fees. Recorded fees/losses in cash count.
It remains an admission snapshot, not an atomic venue-wide reservation against external trades.

## Consequences and alternatives

- Retained fill/latch evidence closes stop, replacement, and allowed deletion bypasses without
  accumulating mutable account totals or guessing historical prices.
- Incomplete legacy/opening evidence may require reconciliation; resets cannot invent evidence.
- Strict missing-mark behavior is preferable to charging overnight lifetime PnL to a new UTC day.
- Keeping paper evidence consumes storage; financial evidence is not automatically purged.
- Reusing the exposure set, mode-wide unrelated drawdown, implicit stop/midnight resets, and
  USD/USDC conversion without rates were rejected.
