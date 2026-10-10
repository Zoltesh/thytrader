# 0130: Fleet entry health

- Status: Accepted
- Date: 2026-10-10
- Relates to: [0114](0114-readiness-preflight-and-venue-reconciliation.md),
  [0115](0115-durable-safety-alerts-and-supervision.md),
  [0117](0117-truthful-inventory-and-fleet-controls.md),
  [0120](0120-verified-risk-opening-evidence.md),
  [0125](0125-correlation-aware-risk-limits.md),
  [0129](0129-paper-futures-books-and-shared-collateral-risk.md)

## Context

On 2026-10-10 one legacy order in a stopped live book blocked every live USDC entry for days,
and nobody could see it. The order was FILLED with `filled_quantity` `0` but had one applied
fill of 0.00014174, so `risk.opening_accounting._orders_covered` failed,
`reconstruct_day_open` returned `None`, and the daily-loss breaker failed closed with
`BREAKER_MARK_MISSING` for every live USDC entry. Operator `health`, `risk` and `readiness`
looked healthy apart from three paused bots; only per-bot `decisions` rows showed the block.

The entry gate fails closed on purpose (unknown evidence denies new risk). The defect was that
a fail-closed denial that applies to *every* entry was only ever evaluated, and recorded, one
bot and one signal at a time. Standing instruction: nothing may block or degrade the fleet
unnoticed.

## Decision

1. **Fleet entry readiness evaluator** (`risk.fleet_entry_health`, pure). For every mode and
   quote/settlement scope (`USD`, `USDC`, `USDT`, `CFM-USD`) with a running or paused book, it
   runs the gate's own checks with a neutral probe entry: zero notional, the scope's BTC
   reference product (β 1), and a strategy id no book has. Reusing the gate's functions
   (`unresolved_accounting_verdict`, `evaluate_circuit_breakers`, `beta_verdict`,
   `collateral_verdict`, `cluster_verdict`, `linked_breaker_verdict`, `_exposure_verdict`,
   `quote_scoped_snapshots`) means the report cannot disagree with the gate. Checks:
   `fleet_disarm`, `accounting_inventory` (any unreadable book: admission reloads them all),
   `quote_scope`, `venue_quote_balance`, `futures_collateral`, `open_position_slots`,
   `exposure_cap`, `btc_beta`, `unresolved_accounting`, `daily_loss` (evidence and latch, every
   book the breaker includes: running, paused and stopped), `drawdown_latch` (strategy-scoped,
   never fleet-wide), `linked_futures_breaker` and `entry_cluster`. Each scope gets
   `entries_admissible: yes | blocked | unknown`, the fleet-wide reason codes and the blocking
   books. `risk.opening_diagnosis` replays the opening-accounting rules and names the failed
   one, for example `order X FILLED with filled_quantity 0 but fills sum 0.00014174`.
   `execution.fleet_entry_evidence` loads its inputs exactly as entry admission does (fresh
   accounting snapshots with recovered midnight marks, last-close marks, β evidence, the CFM
   mirror, the disarm latch). A failed read is `unknown`, never a pass.
2. **Blocker classes** say what clears a block: `evidence` (a repair), `latch` (a breaker
   reset), `policy` (manual futures on the shared collateral without a reserve), `capacity`
   (slots or exposure caps full; an exit), `transient` (the clustering window) and `operator`
   (a fleet disarm; a rearm). The first three are alertable.
3. **Fleet decision-log aggregation** (`operator.fleet_decisions`). For every running book it
   reads 24 h of `entry_blocked` and `skipped` decisions, groups them by reason, and flags
   systemic blockers: `evidence_block` (any evidence-type code), `shared_reason` (the same
   non-routine reason on 2+ bots), `sizing_skips` (a sizing skip such as
   `NOTIONAL_BELOW_MINIMUM` on 2+ bars) and `warmup_stuck` (12+ warmup skips on one bot).
4. **Surfaces.** A new operator `fleet-health` report (`GET /api/v1/operator/fleet-health`);
   `fleet_entries` sections and components in `readiness` and `risk`; a `fleet_entries`
   component in `health`; a prominent Home banner; and a durable `FLEET_ENTRIES_BLOCKED`
   alert (scope `fleet`, subject `fleet:<mode>:<scope>`) in the ADR 0115 feed. The execution
   worker evaluates the fleet once per cycle after per-book supervision; the alert is raised
   on the first evaluation that finds an alertable block, deduplicated while it lasts,
   resolved on the first complete evaluation without one, and delivered through the configured
   notify provider. It is `critical` for a live scope blocked by evidence, else `warning`.
   Unknown checks never resolve it.
5. **Health stays cheap.** `health.fleet_entries` reads the open fleet alerts (the worker's
   evaluation), failing on a critical one and degrading on a warning one or an unreadable
   feed. `fleet-health`, `readiness` and `risk` evaluate fresh in the API process.
6. Ops contract `thytrader-ops-contract-v89` advertises `runtime_observability:
   fleet_entry_health`. No migration: alert `code` and `scope` columns are unconstrained text.

## Gate visibility audit (2026-10-10)

| Gate | Noticed before? | Now |
| --- | --- | --- |
| Daily-loss evidence missing (`BREAKER_MARK_MISSING`) | No (decisions only) | Report, sections, health, alert, banner |
| Unreadable accounting snapshot (all entries denied) | No | Same |
| Unresolved accounting | Per-book readiness finding only | Same, as a fleet block |
| Daily-loss latch | Yes (`BREAKER_LATCHED`) | Also as a fleet scope block |
| Drawdown latch | Yes (`BREAKER_LATCHED`) | Listed, strategy-scoped |
| BTC beta unavailable (ADR 0125) | No | Fleet block and alert |
| β-weighted exposure at cap | No | Reported (`capacity`) |
| Futures collateral unknown / in use (ADR 0129) | State only, not the block | Fleet block and alert |
| Venue quote balance unknown | No | Per book; fleet block and alert when no book has one |
| Mixed or unsupported quote on a book | No | Fleet block and alert |
| Linked futures daily-loss breaker | No | Fleet block and alert |
| Fleet clustering cap full | No | Reported (`transient`) |
| Open-position slots / exposure cap full | Counts only | Reported (`capacity`) |
| Fleet disarm | Runtime fleet status | Reported (`operator`) |
| Data gap / missing candles, live broker missing, user-feed gate | Yes (pause, `BOOK_PAUSED_MISMATCH`) | Unchanged |
| History cache warming stall | Yes, as `DECISION_DEADLINE_MISSED` | Unchanged |
| Indicator warmup stuck | No | `warmup_stuck` systemic flag |
| Lease loss, repeated cycle failures | Yes (`WORKER_LEASE_STALE`, `WORKER_BOOK_FAILURES`) | Unchanged |
| Reference data stale/missing, signal stale, per-product caps | Decisions only | Flagged when shared by 2+ bots |

## Consequences

- A fleet-wide block now shows in every surface an operator or agent starts from, with the
  exact record to repair, and pages through the notify provider.
- The worker reads every book's accounting snapshot once more per cycle and β and marks from
  its caches; the API does the same per `fleet-health`, `risk` or `readiness` call.
- A fully invested fleet shows a warning banner and degrades the `readiness` and `risk`
  `fleet_entries` component (`FLEET_ENTRY_CAPACITY_FULL`), but never pages or fails health.
- Known gaps: the futures scope runs the shared checks but not the futures gate's own envelope
  (margin, funding, contract binding; `futures-books` reports those); decision-log flags are
  not alerted; a single bot's persistent reference-data or freshness block is visible only in
  its decisions; a failed supervision pass is logged, not recorded as a heartbeat.

## Alternatives considered

- **Alert from the decision log only.** Rejected: it needs a signal to fire first, and a block
  can last days between signals; the gate's own functions answer without one.
- **Compute health's component fresh.** Rejected for cost on the most-called report; the
  worker already evaluates every cycle with the gate's evidence.
- **Alert every fleet-wide block.** Rejected for capacity, clustering and a deliberate disarm:
  they clear by themselves or were chosen, and paging on them would train operators to ignore
  the alert.
