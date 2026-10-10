# 0129: Paper futures books and risk on a shared USDC collateral pool

- Status: Accepted
- Date: 2026-10-10
- Relates to: [0033](0033-phase-10-risk-policy-registry.md),
  [0050](0050-daily-loss-drawdown-rate-collars.md),
  [0106](0106-account-risk-capital-and-live-startup-baselines.md),
  [0117](0117-truthful-inventory-and-fleet-controls.md),
  [0120](0120-verified-risk-opening-evidence.md),
  [0121](0121-execution-write-boundaries.md),
  [0125](0125-correlation-aware-risk-limits.md),
  [0127](0127-cfm-futures-account-mirror.md),
  [0128](0128-futures-backtest-model.md)
- Extends: 0106 (capital base under futures collateral use), 0125 (beta cap with futures)

## Context

ADR 0127 §8 records an observed fact (2026-10-10): with futures enabled, the live account showed
`futures_buying_power` 514.24 against `cbi_usd_balance` 0.01 and `cfm_usd_balance` 0. **Coinbase
counts the USDC spot balance as CFM futures collateral.** The P0 plan assumed USDC could not
margin CFM and only linked futures with USD-quoted spot books. That assumption is wrong:

- Futures margin and the USDC spot books ThyTrader trades draw on **one collateral pool**.
- A futures loss, margin increase (the 16:00 ET overnight step-up) or liquidation consumes USDC
  that a spot book's allocation, the ADR 0106 risk capital and the exposure caps assume is free.
- Futures amounts are USD; spot books are USDC. The invariant "USD, USDC and USDT are never added
  together" stands. The pool must be respected **logically** (gates, links, disclosure), never by
  adding the two currencies into one number.

Today futures are traded only by hand (ThyTrader has no futures order path), and P1 adds paper
futures books. So two problems need rules now: live spot books beside manual futures, and paper
futures books beside paper spot books. Live futures books are P2 and out of scope here.

Unverified and therefore treated conservatively until observed:

- Whether the venue's *available* USDC drops when CFM margin is in use (an auto-transfer that
  converts USDC into CFM USD), or whether margin is only a claim on it. Until a position shows
  it, ThyTrader cannot read futures margin use off the USDC balance.
- Whether USDT also counts as collateral. Assumed **not**; USDT stays an unlinked scope until
  observed.
- The margin-window types read `..._UNSPECIFIED` while flat.

## Decisions (2026-10-10)

The lead reviewed and accepted this ADR with three answers:

1. Live USDC/USD spot entries are denied by default while manual futures are `in_use` (L2),
   with the opt-in reserve (L3) exactly as written.
2. `peg_haircut` defaults to 1.25 with a minimum of 1.0.
3. Base-unit netting ships in P1 as the opt-in `futures.beta_netting: net_by_underlying`, for
   managed same-mode books only (paper in P1). External or manual hedges never net.

## Decision

### 1. Scopes and the collateral link

- **Settlement scopes** stay separate currencies: spot USDC, spot USD, spot USDT, and futures USD
  (CFM). Every cap, capital base, daily-loss figure and report total is computed within one scope.
- **Collateral-linked scopes:** spot USDC, spot USD and futures USD form one *collateral group*
  when the account has futures enabled. Spot USDT is not in the group.
- Links between scopes are **logical only**: one scope's state can deny entries in another, and
  reports put each scope's figures side by side with `collateral_note`. No rule converts or adds a
  USD amount to a USDC amount, and no report prints a combined total.
- The one sanctioned comparison across currencies is a **threshold check under a declared peg**
  (§2, the reserve check): a USDC amount the operator declares is compared with a USD margin
  figure multiplied by a haircut ≥ 1. It produces a yes/no, never a sum, and both numbers are
  reported in their own currencies.

### 2. Live spot books beside manual futures (ships first, P1-2a)

The mirror (ADR 0127) classifies the futures account each cycle:

| State | Condition |
|---|---|
| `absent` | No snapshot, futures `not_enabled`, or no credentials |
| `idle` | `enabled`, snapshot ≤ 180 s old, no positions, `initial_margin` 0 or null-with-no-positions, `total_open_orders_hold_amount` 0, no nonterminal futures orders |
| `in_use` | `enabled` and any position, positive `initial_margin` or a positive open-order hold |
| `unknown` | `enabled` (or last seen enabled) with a stale snapshot or a failed balance or position read |

Rules for new risk-increasing **live** entries in collateral-linked spot scopes (USDC, USD):

- **L1 (`unknown`).** Deny with `FUTURES_COLLATERAL_UNKNOWN`. Unknown use of the shared pool is
  never treated as zero.
- **L2 (`in_use`, default).** Deny with `FUTURES_COLLATERAL_IN_USE`. While manual futures hold
  margin on the shared pool, spot bots do not add risk.
- **L3 (`in_use`, opt-in reserve).** If the policy sets
  `futures.live_spot_collateral_reserve_quote` (an amount in the policy quote), entries continue
  when both hold:
  - the reserve check: `reserve_quote ≥ initial_margin_usd × futures.peg_haircut` (default 1.25,
    minimum 1.0), else deny `FUTURES_COLLATERAL_RESERVE_SHORT` with both numbers in their own
    currencies;
  - the reserve is withheld from spot capital (§3).
- **L4 (`idle` or `absent`).** Today's behaviour.
- Protective exits, cancellations and managed shutdown are never gated (as everywhere).
- Paper spot books are not affected by the live account's futures.
- In-kind adoption (ADR 0124) is not gated: it spends no quote and sends nothing to the venue.
- The gate covers every quote-funded live entry: strategy, lockstep, portfolio-sleeve and
  discretionary entries, pyramid adds and reprices. The execution worker and the API bind the
  mirror store; an install without a database or credentials has no mirror (`absent`).

### 3. ADR 0106 account capital

The live capital base stays one currency: venue available policy-quote balance + managed
inventory cost + working buy reservations. Under futures:

- It **never** includes `futures_buying_power`, `cfm_usd_balance`, `cbi_usd_balance`, futures
  unrealized PnL or funding. Futures buying power is the same USDC, not extra capital.
- When L3 applies, `futures.live_spot_collateral_reserve_quote` is subtracted from the venue
  available input (a same-currency subtraction). Exposure fractions, the per-product cap,
  `min_available_quote_reserve` and daily loss therefore use the reduced base. If the reserve
  exceeds the available balance, capital is non-positive and entries are denied.
- Paper keeps `paper_capital_quote` for spot and gains `futures.paper_capital_usd` for futures
  books (§4); the two paper envelopes are separate simulated money.

### 4. Paper futures books (P1-4)

- **Binding.** A paper futures deployment binds the contract at start (Alembic 0073
  `deployment_instrument_contracts`: product, kind, contract size, settlement currency, expiry,
  catalog fingerprint) and never re-reads it.
- **Capital.** Books draw from `futures.paper_capital_usd`; an unset value refuses a paper
  futures start.
- **Funding.** At each settled funding hour the book's cash changes by −quantity × mark × rate in
  the same row-locked transaction as fills (ADR 0121), recorded once in
  `futures_funding_entries` (unique on deployment, product, funding time). A missing settled hour
  pauses new entries for that book (`FUNDING_HISTORY_MISSING`) and is retried; it is never
  zero-filled.
- **Liquidation monitor.** Each cycle, if book equity < maintenance (overnight rates; maintenance
  = initial unless calibrated), the worker submits a paper `LIQUIDATION`-purpose exit at mark.
  `IntentPurpose.LIQUIDATION` is new and is a protective purpose (never gated).
- **Evidence.** ADR 0120 opening-equity reconstruction reverses funding entries with fills
  (`per_product_applied_fills_and_funding_v1`). Unapplied funding is unknown evidence and denies.
- **Live.** Starting a live futures deployment returns `FUTURES_LIVE_UNSUPPORTED` (P2).

### 5. The futures entry gate (P1-5)

`RiskPolicyDefinition.futures` (optional; excluded from the canonical document while unset, so
existing policy fingerprints are unchanged):

| Field | Meaning |
|---|---|
| `paper_capital_usd` | Paper futures envelope (USD) |
| `max_leverage` | Σ\|notional at mark\| / futures equity, per mode |
| `min_liquidation_buffer_fraction` | After the entry: (equity − maintenance) / equity ≥ this |
| `max_exposure_fraction` | Gross futures notional / futures capital |
| `max_order_contracts` | Per order |
| `max_hourly_funding_rate_abs` | Deny entries when the latest settled \|rate\| exceeds it |
| `daily_loss_limit_fraction`, `max_daily_loss_usd` | Futures-scope daily loss (§7) |
| `max_btc_beta_exposure_fraction` | Futures-scope beta cap (§6) |
| `beta_netting` | `gross` (default) or `net_by_underlying` (§6) |
| `live_spot_collateral_reserve_quote`, `peg_haircut` | §2 L3 |

Rules for a futures entry:

- An unset `futures` block denies every futures entry (`FUTURES_POLICY_UNSET`).
- Admission uses **overnight** rates on every day, including weekends, never intraday.
- Unknown margin rates, funding, contract binding or marks deny; nothing defaults to zero.
- Leverage must be ≤ the lower of the policy's `max_leverage` and the strategy's.
- The entry-rate, fleet clustering and collar gates apply as for spot.
- Protective and liquidation exits are never gated.

### 6. Exposure caps and the beta cap (ADR 0125)

- **Exposure caps** stay per scope: spot caps over spot books in the policy quote (unchanged), and
  futures caps (`max_exposure_fraction`) over futures books in USD. There is no combined cap.
- **Beta, default `gross`.** Futures books have their own USD-scope beta cap
  (`futures.max_btc_beta_exposure_fraction` × futures capital, Σ|futures notional| × β). The spot
  beta cap is unchanged: a futures short never reduces it. β of an underlying is the same unitless
  estimate ADR 0125 computes for `<underlying>-<policy quote>`.
- **Beta, opt-in `net_by_underlying`.** For the **spot policy-quote** beta cap only, a *managed*
  futures position in the *same mode* nets against same-mode managed spot inventory of the same
  underlying **in base units**: net = spot base (signed) + futures base (signed, contracts ×
  contract size). The net is then valued at the policy-quote spot mark of
  `<underlying>-<policy quote>` and multiplied by β. No USD amount enters the USDC figure.
  Conditions, each failing closed to gross:
  - only ThyTrader-managed futures books; external (manual) CFM positions never net, because
    they can be closed at any time without the gate knowing;
  - the futures book's mark, contract binding and funding evidence are known, and the spot mark of
    `<underlying>-<policy quote>` exists and is fresh;
  - the futures leg still passes its own gross futures-scope checks (leverage, liquidation
    buffer, futures beta cap); netting never relaxes the hedge leg itself.

  Netting is a hedge-aware view for the spot cap, not permission to over-lever: with netting, a
  spot entry may use room freed by a hedge, but a hedge that is later liquidated restores gross
  exposure, so §5's liquidation buffer is what protects the netted book.

### 7. Daily loss: per scope, truthful about the pool

- Daily loss is computed **per settlement scope** in its own currency: each spot quote as today;
  futures USD as the change in futures-book equity (fills, fees and funding) since the UTC day
  open, against `futures.daily_loss_limit_fraction` × futures capital and the optional
  `futures.max_daily_loss_usd`.
- **Linked breakers.** Within one mode, a latched daily-loss breaker in any collateral-linked
  scope also denies new entries in the other linked scopes, with
  `SHARED_COLLATERAL_BREAKER` naming the latched scope, its loss and its limit in that scope's
  currency. The loss figures are never added together. Each scope's own breaker and reset are
  unchanged; protective exits continue.
- Live, with manual futures: the mirror's futures `daily_realized_pnl`, `unrealized_pnl` and
  `funding_pnl` (USD) are reported beside the USDC daily loss in readiness and the operator risk
  report with `collateral_note`. They are not a ThyTrader breaker (the positions are unmanaged);
  §2 governs spot entries instead.

### 8. Reporting

The operator `risk` and `readiness` reports add a `collateral_group` block: the futures state
(§2), each linked scope's capital, exposure and daily loss **in its own currency**, the active
link rules and `collateral_note`. There is no combined total anywhere.

### 9. Revised P1 slicing (for the USDC finding)

| # | Slice | Change from the plan |
|---|---|---|
| P1-1 | ADR 0128 schema and run spec | unchanged |
| P1-2 | Kernel: contracts, margin, funding, liquidation | unchanged; results disclose `collateral_note` |
| **P1-2a** | **Live spot collateral gate (§2, §3) and the `collateral_group` report block** | **new; ships first and independently**, because manual futures already share the pool with live spot bots. Needs only the P0 mirror. Policy fields `live_spot_collateral_reserve_quote` and `peg_haircut` (the rest of the `futures` block can land later; unset keeps L1/L2 behaviour). |
| P1-3 | Fee evidence (`transaction_summary` FUTURE, 1-contract `orders/preview` probe) | unchanged; the probe is a POST and gets its own allowlist and review |
| P1-4 | Paper futures books, Alembic 0073, funding entries, liquidation monitor | linked breakers (§7) wired for paper |
| P1-5 | `RiskPolicyDefinition.futures` block and futures gate | adds §6 netting and §7 links; property test that USD and USDC are never summed in any capital, exposure or daily-loss path |
| P1-6 | Runtime lane, operator, web, skills | Home and readiness show the collateral group in separate currencies |

Before P2 (live futures), verify with one supervised 1-contract position how CFM draws on USDC
(whether `available` USDC drops), and calibrate maintenance against `liquidation_threshold`.

## Implementation notes

- **P1-4 (paper futures books).**
  - Paper books trade perp-style contracts only. A dated contract would need the backtest's
    expiry flatten, which the runtime does not run yet, so its start is refused
    (`FUTURES_PAPER_UNSUPPORTED`).
  - A paper futures start names its maker and taker rates and a per-contract fee (USD); the
    account's spot rates never stand in. The contract binding is written right after the book
    row; if it cannot be written the book is stopped and the start fails.
  - The policy block gains `paper_capital_usd` (§4) and `daily_loss_limit_fraction` (the futures
    scope of §7) in P1-4; the remaining §5 fields arrive with P1-5. Until then admission uses
    the strategy's `max_leverage`, overnight rates with maintenance = initial, and a 0.5
    liquidation buffer.
  - Funding is book cash movement in the execution store (`DeploymentSnapshot.funding`), applied
    under the deployment row lock with a revision bump. The hours due are derived from applied
    fills (a fill on the bar `[T − 1h, T)` is held at `T`, as in the backtest kernel), so there
    is no cursor. Each hour uses the settled rate and the close of the decision bar containing
    it. A missing hour stops the run of hours; 75 minutes after the hour it is overdue and new
    entries are denied (`FUNDING_HISTORY_MISSING`). The book is not paused.
  - The liquidation monitor runs on each closed bar (paper sees closed bars only) at the bar's
    adverse extreme and exits there with `IntentPurpose.LIQUIDATION`, before the stop. This is
    the kernel's conservative rule; §4's "at mark" would be optimistic for a closed-bar book.
  - Futures books are their own breaker scope (`CFM-USD`), so spot buckets never sum or trip on
    them. Opening evidence of a book with funding is `per_product_applied_fills_and_funding_v1`.
  - Linked breakers (§7) are wired for paper in both directions. Live spot is not linked to
    manual futures (they are unmanaged); §2 governs there.
- **P1-5 (futures gate).**
  - The §5 fields are optional and excluded while unset (existing policies keep their
    fingerprints). `min_liquidation_buffer_fraction` replaces the fixed 0.5 (still the
    default); `max_leverage` is combined with the strategy's (the lower wins) in the book's
    margin terms. `beta_netting` unset means `gross`.
  - `max_exposure_fraction` and `max_btc_beta_exposure_fraction` are gross notional over the
    futures envelope and may exceed 1 (up to 20), since futures are levered.
  - `max_daily_loss_usd` binds the futures scope in paper as well (futures books are paper
    only); the spot `max_daily_loss_quote` stays live-only.
  - The funding-rate cap reads the newest settled hourly rate of the last day; unknown denies
    with `FUNDING_HISTORY_MISSING`.
  - Netting compares the netted base-unit figure, valued at the policy-quote spot mark, with
    the gross position cost and only ever lowers the spot figure: a same-direction futures
    leg is not added to the spot cap (the futures scope has its own). Working entries stay
    gross. A futures book with no readable binding disables netting for every underlying.
  - USD/USDC never-summed is checked by a seeded property test over random fleets of every
    scope (scoping, capital and the daily-loss breaker).

## Consequences

- With the defaults, any manual futures position pauses new live spot entries in USDC and USD
  books until it is closed or the operator declares a reserve. This is deliberately conservative:
  it is the only rule that cannot mis-size the shared pool without knowing how CFM draws on USDC.
- No number in ThyTrader adds USD to USDC. Shared collateral is enforced by links and shown in
  words.
- Netting is opt-in and limited to managed same-mode books with known evidence; external hedges
  never relax a cap.
- Policies without a `futures` block keep their fingerprints and behaviour, except that L1/L2
  apply when the live account's futures are `unknown` or `in_use` (a behaviour change the
  P1-2a ops contract bump announces).

## Alternatives considered

- **Convert USD to USDC at 1:1 and add.** Rejected: it violates the currency invariant and hides
  depeg risk; the declared-peg threshold check (§2) is the only cross-currency comparison, and
  it never produces a total.
- **Ignore manual futures (they are unmanaged).** Rejected: they consume the same USDC the spot
  gate counts as free.
- **Subtract futures margin from USDC capital automatically.** Rejected until the draw mechanism
  is observed; it would be a hidden conversion and might double count if the venue already moved
  the USDC.
- **Net external hedges in the beta cap.** Rejected: ThyTrader cannot keep an unmanaged hedge in
  place.
- **One combined daily-loss figure.** Rejected: it would sum currencies; linked breakers give
  the same protection truthfully.
