# 0107: Capital-normalized live performance and durable drawdown

## Status

Accepted — 2026-10-03.

Supersedes in part [0050](0050-daily-loss-drawdown-rate-collars.md)'s drawdown calculation
and [0058](0058-protection-lifecycle-accounting.md)'s performance-capital interpretation.
[0106](0106-account-risk-capital-and-live-startup-baselines.md)'s account risk capital,
zero-based live ledger, and UTC-day loss accounting remain in force.

## Context

Live strategy cash is a fill ledger starting at zero. Its equity is trading PnL, not funded
account equity. Dividing losses by that ledger's nonpositive high-water mark returned zero
drawdown and left return unknown, even for a losing bot with a known allocation. Replacing cash
or the opening balance with an allocation would change recorded PnL and daily-loss evidence.
Using the latest allocation or venue balance would let a rebalance or another bot's orders
reset the performance denominator.

## Decision

Persist a separate positive `performance_capital_quote` per deployment. Paper pins its starting
cash; allocated live pins its opening allocation. Unallocated live pins its first known positive
venue sizing balance before entry. Existing funded ledgers retain their positive opening balance;
other legacy books pin their first verified allocation or sizing balance after upgrade. The
original historical funding budget cannot be recovered when it was never recorded. Later
allocation and venue changes do not alter a pinned budget. Invalid pinned metadata stays unknown.

Let `C` be that budget, `S` the recorded ledger opening balance, `E` current ledger equity, and
`H` its durable high-water mark (including the current equity). Return is `(E - S) / C`.
Current drawdown is `(H - E) / (C + H - S)`. The strategy breaker uses current drawdown and the
existing policy threshold, pauses new risk, and preserves its durable latch. Protective exits
continue under the existing execution policy. Missing marks, missing positive capital, or a
nonpositive funded peak deny new entries with `BREAKER_MARK_MISSING`.

Reported maximum drawdown includes recorded fill-event marks, the current close, the durable
peak, and `performance_maximum_drawdown_fraction`, the worst verified fraction observed by
workers. Fill replay marks each product at its own most recent fill price, never another
product's price. Recovery, restart, and rebalance retain the observed maximum. These observations
are not a complete historical candle equity curve; no missing historical intrabar peaks are
invented. An explicit breaker reset clears the latch without clearing the peak or performance
history and does not resume the bot; a continuing breach can trip again.

Ledger cash, net dollar PnL, recorded fees, initial/baseline equity, UTC-day-open equity,
historical decisions, allocation sizing, portfolio limits, and account exposure capital keep
their existing meanings. A missing current mark leaves reported return/drawdown unknown while
retaining the stored maximum for the next complete observation.

## Consequences

Alembic `0061` adds two nullable decimal-text columns without backfilling or rewriting historical
rows. Old writers can operate during rollout. Downgrade refuses to discard populated performance
metadata. Deployment HTTP `capital` exposes both fields; operator performance and portfolio sleeve
metrics use the shared ledger calculation. Bot detail labels ledger equity and pinned performance
capital separately. Ops contract v64 advertises `capital_normalized_performance` and both fields.
No CLI flags, published policy bytes, confirmation gates, or live-arming rules change.

Tests cover fee-bearing losses before first profit, recovery, rebalance, separate product marks,
missing marks/budgets, legacy funded ledgers, HTTP/operator values, worker pause before order
creation, PostgreSQL reload and latch reset, forward migration, and guarded downgrade.

## Alternatives considered

- Fund live ledger cash or rewrite historical opening balances: changes economic evidence.
- Divide by current allocation or venue balance: changes returns and loss limits during rebalance.
- Use PnL high-water as the denominator: misses pre-profit losses and magnifies small profit peaks.
- Reconstruct every historical candle: unavailable marks cannot establish a reproducible history;
  retain verified observations and disclose their limits.
