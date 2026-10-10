# 0133: CFM supervised-trade evidence and the fixed per-contract fee

- Status: Accepted
- Date: 2026-10-10
- Relates to: [0127](0127-cfm-futures-account-mirror.md),
  [0128](0128-futures-backtest-model.md) (amends P1-3b),
  [0129](0129-paper-futures-books-and-shared-collateral-risk.md)

## Context

The futures models of ADRs 0127–0129 were built from documentation, the catalog and read-only
account endpoints. In 2026-10, a supervised one-contract round trip on a CDE nano
perp-style contract (contract size 0.1 of the underlying, market orders on both sides) was
recorded minute by minute by the account mirror's history (ADR 0127 §10), the venue's fills
endpoint, the futures `transaction_summary` and one `orders/preview`. It showed:

1. **Commission.** Each fill paid `notional x the futures tier rate + a fixed $0.11 per
   contract`, at the Intro futures tier (0.10% taker, 0.095% maker). For example, one 0.1 ETH
   contract at 2510 pays 0.001 x 251 + 0.11 = 0.361, and at 2505.5 pays 0.25055 + 0.11 =
   0.36055, exactly. A round trip on one contract therefore costs about $0.72, a move of
   roughly 0.29% of notional before it breaks even.
2. **The preview is all-in.** `orders/preview` reports `commission_total` as that whole amount,
   rounded to cents (0.36 for the 2505.5 example), and itemizes it in
   `commission_detail_total`: `client_commission` (the rate part, 0.2505), `venue_commission`
   (0.10), `clearing_commission` (0.01) and `regulatory_commission` (0); GST and withholding
   lines are taxes and were zero. `est_average_filled_price` carries the price the preview
   assumed (the best ask for a buy, the best bid for a sell).
3. **Collateral draw.** At the fill Coinbase transferred the USD initial margin from the spot
   USDC/USD balances into the CFM-side CBI USD balance, shown as
   `total_pending_transfers_amount` for the whole holding period, and transferred it back
   after the close. During the hold, futures buying power was spot USDC + CBI USD − initial
   margin − the opening fee. The
   CFM `daily_realized_pnl` reports the gross price PnL; fees are not netted in it.
4. **Margin.** Initial margin matched the catalog's overnight short rate (about 30% of
   notional). `liquidation_threshold` was 0.88 x initial margin.
5. **Margin window.** `margin_window_type` stayed `MARGIN_WINDOW_TYPE_UNSPECIFIED` throughout,
   and initial margin did not change at 16:00 ET: no intraday-to-overnight step-up was observed
   on the perp-style contract.
6. **Funding.** `funding_pnl` stayed 0 for a hold of about 1.5 hours, including after the
   close: Coinbase does not book accrued funding to `funding_pnl` hourly.
7. **Mirror reads.** Transient `balance_summary` transport failures recovered on the next
   minute and were surfaced as `FUTURES_READ_FAILURES` in the futures-account history report.

Paper books and the backtest kernel charge `notional x maker/taker rate + fee_per_contract x
contracts` (ADR 0128, 0129), which matches item 1. But the operator `fees` report filled
`fee_per_contract` with the preview's `commission_total` per contract (ADR 0128 P1-3b), the
all-in amount. An operator following the skills counted the rate twice: about $0.61 per side
modeled against $0.36 billed, a 70% overstatement. No paper futures book had been started, so
no stored book or result carries the inflated fee.

## Decision

1. **`fee_per_contract` is the fixed part, excluding the rate.** With
   `fees --futures-preview-product-id`, the `futures` block keeps the raw all-in quote as
   `preview_commission_total` and reports:
   - `fee_per_contract` from the itemized `venue + clearing + regulatory` commissions when the
     preview carries a valid itemization (`fee_per_contract_source: orders_preview_itemized`,
     also shown as `preview_fixed_commission_itemized`). This is exact; the rounded total is
     not.
   - Otherwise `commission_total - preview_rate_commission`, where `preview_rate_commission` =
     futures `taker_fee_rate` x `preview_contract_size` (the catalog contract) x
     `preview_price` (`est_average_filled_price`); source `orders_preview_less_taker_rate`.
   - `preview_rate_commission`, `preview_price` and `preview_contract_size` are reported
     whenever they are known, so the decomposition is visible.
   - When the fixed part cannot be derived, `fee_per_contract` stays null
     (`operator_input`) with `fee_per_contract_unavailable_reason`
     (`taker_fee_rate_unavailable`, `preview_price_unavailable`, `contract_size_unavailable`,
     or `negative_fixed_fee` when the rate part exceeds the total). An itemization counts
     only when every line is a non-negative amount and the lines (rate part, fixed parts and
     taxes) add up to `commission_total` within a cent; otherwise it is ignored. Nothing is guessed.
   The `orders_preview` source token is retired. The probe still sends only the one
   allowlisted preview POST and places no order. Ops contract v93; the browser's "Quote fee
   per contract" fills the same fixed figure.
2. **Collateral.** The spot USDC (and USD) balance is the collateral pool; the CBI USD figure
   during a hold is a transfer from it, not new money. This confirms ADR 0129's shared-pool
   model: the two are linked by rules and never summed.
3. **Margin.** Paper and backtests keep `maintenance_fraction_of_initial` = 1.0 as the
   conservative default: they treat initial margin as the liquidation line, while the venue's
   observed threshold was 0.88 x initial. Operators may declare 0.88 in a backtest, which
   liquidates later.
4. **Margin window.** Paper keeps sizing and marking with the overnight rates, which for this
   contract equal what the venue charged around the clock. Dated contracts may still step up
   at 16:00 ET; that is unobserved.
5. **Funding** stays modeled hourly in paper and backtests (ADR 0128, 0129). When Coinbase
   books accrued funding to `funding_pnl` or the balance is an open question; the mirror's
   history is the place to observe it.
6. **Mirror.** No change: a failed read is surfaced as `FUTURES_READ_FAILURES` and is never
   filled with zeros.

## Consequences

- Paper futures books and backtests started from the `fees` report now charge what Coinbase
  bills. For a 0.1 ETH contract the modeled per-side fee falls from about $0.61 to $0.36.
- A strategy's futures fees are dominated by the fixed part at small notionals: $0.11 is
  about 0.044% of a $251 contract, almost half the taker rate. Backtests of small
  contracts must carry it.
- Operators must not paste `preview_commission_total` into `--fee-per-contract` or
  `futures.fee_per_contract`. The skills say so.
- A preview without `est_average_filled_price` or itemization, with an unread catalog,
  leaves `fee_per_contract` null rather than a wrong number.

## Alternatives considered

- **Keep the all-in preview figure and drop the rate from paper fills.** Rejected: the rate
  part scales with price and contracts, so a one-contract quote at one price is wrong at any
  other, and backtests span years of prices.
- **Subtraction only.** Rejected as the primary path: the preview rounds `commission_total` to
  cents (0.36 instead of 0.36055), which leaves 0.10945 instead of the exact 0.11. The
  itemization is exact; subtraction remains the fallback.
- **Use the best bid or ask as the price when the estimate is missing.** Rejected: the fallback
  would be a guess about which side the preview priced; the report leaves the fee null instead.
- **Set `maintenance_fraction_of_initial` to the observed 0.88.** Rejected as a default: one
  observation on one contract, and 1.0 errs toward liquidating early.
