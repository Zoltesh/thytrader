# 0128: Futures in the strategy schema, run spec and backtest kernel

- Status: Accepted
- Date: 2026-10-10
- Relates to: [0083](0083-unified-backtest-model.md),
  [0090](0090-research-correctness-optional-take-profit-diagnostics.md),
  [0109](0109-research-publication-projections-and-campaigns.md),
  [0126](0126-futures-instrument-catalog-read-only.md),
  [0127](0127-cfm-futures-account-mirror.md),
  [0129](0129-paper-futures-books-and-shared-collateral-risk.md)
- Extends: 0083 (the unified backtest model keeps `engine: "thytrader-backtest"`; new fields
  appear only in futures specs)

## Context

Phase P0 made Coinbase CFM futures observable: the contract catalog (ADR 0126), hourly funding
history from the day the poller started, 24/7 futures candles (volume in contracts) and a
read-only account mirror (ADR 0127). Phase P1 adds futures to research and paper. This ADR covers
the deterministic parts every mode shares: the strategy document, the run spec and the backtest
kernel. ADR 0129 covers paper books and risk.

Facts that shape the model:

- Sizes are in **contracts**; one contract is `contract_size` of the underlying (0.01 BTC for
  `BIP`, 0.1 ETH for `ETP`). The minimum order is one contract.
- Perp-style contracts charge funding every hour; longs pay a positive rate. History exists only
  from 2026-10-10 (the poller's start); there is no venue backfill.
- Margin rates move with volatility. Overnight rates (about 21–30% for BTC/ETH, short higher than
  long) apply from 16:00 ET and all weekend; intraday rates are opt-in. No historical margin
  rates exist.
- Liquidation happens when equity falls below maintenance; Coinbase gives no per-position
  liquidation price.
- **Observed 2026-10-10 (ADR 0127 §8): the USDC spot balance is CFM collateral.** A futures
  backtest's capital is a USD figure, but the money that backs it in reality is the same USDC
  that funds spot books. The kernel models one futures book; the shared pool is a risk-layer
  concern (ADR 0129) and a disclosure here.

## Decisions (2026-10-10)

Reviewed and accepted by the lead with ADR 0129. No changes to this ADR were requested.

## Decision

### 1. Strategy document

- `instrument.kind: "future"` marks a futures strategy. It is omitted for spot, so every
  existing document and its fingerprint is byte-identical.
- A futures instrument names a CDE product id, `quote_currency: "USD"` (the settlement currency)
  and `base_currency` = the venue `contract_root_unit` (the underlying, never the id prefix).
  Validation resolves both from the catalog at save time and refuses a mismatch.
- An optional `derivatives` block: `max_leverage` (≥ 1; the strategy's own ceiling under the
  policy's), `margin_mode: "overnight"` (the only P1 value; intraday is not modelled) and
  `flatten_before_expiry_hours` (dated contracts; required for them).
- Futures documents are single-instrument in P1: no `additional_instruments`, no reference
  instruments on another settlement currency. Multi-leg and spot-plus-futures pairs are P3.
- Shorts are real shorts: a futures `short` entry needs no base inventory.

### 2. Run spec

New fields, each excluded from the canonical document when unset (spot specs are byte-identical,
pinned by goldens):

- `instrument_contract`: product id, kind, contract size, settlement currency, expiry facts and the
  futures-catalog fingerprint the run bound. A run never re-reads the catalog.
- `margin`: the long and short initial-margin rates the run assumes, their source
  (`latest_observation` with its instant, or `explicit`), an optional `stress_multiplier` (≥ 1)
  and `maintenance_fraction_of_initial` (default 1.0: maintenance equals initial, which is
  conservative). Because no margin history exists, the assumption is constant across the window
  and is listed in `validity_limits`.
- `funding_series_fingerprint`: the content identity of the funding rows the run consumed
  (settled hours only), or an explicit `funding_assumption: {constant_rate}` that is disclosed in
  `validity_limits`. A perp run without either fails with `FUNDING_HISTORY_MISSING`.
- `costs.fee_per_contract` beside the existing rate fees; no compiled default for futures fees
  (P1-3 suggests account evidence; a run without fees is refused).
- `initial_capital` stays a number in the strategy's quote (USD). The result discloses
  `collateral_note`: real collateral is the account's USDC spot balance, shared with spot books.

### 3. Kernel

- **Contract quantum.** Base quantity = floor(notional / (price × contract_size)) ×
  contract_size. Below one contract the entry is skipped with reason
  `BELOW_ONE_CONTRACT`; it is never rounded up.
- **Margin sizing.** An entry needs initial margin = |quantity| × price × rate(side) at the
  assumed overnight rate, and must leave equity − maintenance ≥ the run's
  `min_liquidation_buffer_fraction` × equity (default 0.5; ADR 0129 defines the policy value
  for paper). Leverage = Σ|notional| / equity must stay ≤ `max_leverage`. The long-only
  `notional > cash` check does not apply to futures.
- **Ledger.** Quantities are base-equivalent, so the existing cash-and-inventory ledger gives the
  right equity and realized PnL. Fees: rate × notional + `fee_per_contract` × contracts.
- **Funding.** At each funding hour inside an open position: cash −= quantity × hour close × rate
  (longs pay positive rates; shorts receive them). A missing settled hour fails the run with
  `FUNDING_HISTORY_MISSING` naming the hour, unless an explicit constant rate was chosen.
  Funding appears in cost attribution beside fees.
- **Liquidation.** On every bar, at the bar's adverse extreme, if equity < maintenance the
  position is closed at that bar's adverse price (or the gap open if worse) with exit reason
  `liquidation`, before stops and targets of the same bar. This is conservative versus the venue,
  which liquidates at mark.
- **Expiry.** A dated contract is flattened `flatten_before_expiry_hours` before
  `expires_at`; a window past expiry is refused.
- **Spot unchanged.** Spot runs never enter any futures branch; golden fingerprints and result
  bytes are pinned.

## Consequences

- Futures research becomes reproducible from a fingerprinted catalog binding, funding series and
  margin assumption.
- Perp backtests cover only windows after 2026-10-10 unless a constant funding rate is declared;
  results say so.
- A futures result is a USD book in isolation. It cannot show the effect of the shared USDC
  collateral pool on spot books; ADR 0129's paper rehearsal and risk rules carry that.
- Liquidation and maintenance are modelled conservatively; real fills can be better.

## Implementation notes

- **P1-1.** The run spec binds funding as `funding: {series_fingerprint, settled_hours}` or
  `funding: {constant_rate}` (one field instead of `funding_series_fingerprint` and
  `funding_assumption`). Futures documents also may not enable `entry.pyramiding` (added in
  P1-2): one position per futures book.
- **P1-2 (kernel).**
  - The funding rows are a kernel input (`funding_rates`, funding hour → rate) like candles,
    not part of the spec. The kernel requires exactly the hours in (`starts_at`, `ends_at`],
    `settled_hours` of them, hashing to `series_fingerprint` (`funding_series_fingerprint`).
  - Funding hour T is charged on the bar with start < T ≤ end, at that bar's close, when the
    position is still open after the bar's exits. On bars longer than one hour this prices
    every hour at the bar close and is disclosed as `futures_funding_at_bar_close`.
  - `max_strategy_exposure_fraction` (at most 1) caps initial margin committed for futures, not
    notional; leverage caps notional. Every bound holds after the entry fee.
  - The shared-collateral disclosure is the validity limit `futures_shared_usdc_collateral`
    rather than a separate `collateral_note` field, so it travels with the other limits.
  - Liquidation fills at the bar extreme, so a gap can leave equity negative. That is the
    conservative model, and later entries skip with `insufficient_cash`.
  - Entries whose fill bar would start at or after the flatten time skip with
    `expiry_window`.

## Alternatives considered

- **A separate futures engine id.** Rejected: ADR 0083 collapsed engines; the new fields only
  exist in futures specs.
- **Backfill funding from a vendor.** Deferred: it is a separate, separately fingerprinted source
  with its own licensing; not P1.
- **Use intraday margin rates.** Rejected for P1: the 16:00 ET step-up can liquidate an
  intraday-sized position, and intraday enrolment is opt-in at the venue.
- **Express capital in USDC because the collateral is USDC.** Rejected: the futures book settles
  in USD, and converting would sum USD with USDC. The shared pool is handled by risk (ADR 0129)
  and disclosed, not folded into a number.
