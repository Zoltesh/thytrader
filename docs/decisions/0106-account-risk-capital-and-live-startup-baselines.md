# 0106: Account risk capital and live startup baselines

- Status: Accepted
- Date: 2026-10-03
- Supersedes in part: [0058](0058-protection-lifecycle-accounting.md), capital-base scope only
- Relates to: [0033](0033-phase-10-risk-policy-registry.md),
  [0050](0050-daily-loss-drawdown-rate-collars.md),
  [0087](0087-per-bar-decision-timeline.md),
  [0091](0091-portfolio-deployment-limits-and-manager-proposals.md)

## Context

Account-wide exposure and daily loss sum multiple live deployments, but their denominator came
from the current bot's allocation. A 20-quote allocation could therefore cap the entire account
at 20, refusing a 7-quote entry beside another bot's 16-quote inventory even when the account
and published absolute ceiling had room. Changing which bot was evaluated changed the effective
account limit. Separately, new flat live ledgers had no equity baseline until their first worker
cycle; a sibling's entry could fail daily-loss evaluation during startup. The rejection always
claimed a missing inventory mark, even when the missing evidence was a flat book's baseline.

## Decision

Account capital is one observed venue available-quote balance plus the cost basis of managed long
inventory and the remaining quote reserved by working buy entries in risk-bearing live books.
Running, paused, and stopped residual books participate. Inventory cost matches the existing
exposure calculation; this is a cost-basis risk budget, not current account NAV. Do not add
deployment ledger cash, per-bot allocations, repeated copies of the venue balance, short-sale
proceeds already in quote, sell-entry base reservations, or protective exit orders. An unknown or
negative venue balance remains fail-closed. Paper keeps `paper_capital_quote`.

Use that same capital base for account exposure, per-product exposure, and mode-wide daily loss,
including discretionary admission. Bot allocations still bound sizing and per-strategy exposure;
portfolio sleeve and shared portfolio caps still bind independently. Published fractions and
absolute ceilings are unchanged; the strictest applicable cap wins.

Initialize a new strategy deployment's fill-ledger baseline at its opening ledger cash: starting
cash for paper, zero for live. Venue balances and allocations stay separate. Zero is valid baseline
evidence and must not be interpreted as missing. Existing unknown baselines and missing inventory
marks continue to deny entries. Explain which deployment lacks which evidence; exposure rejections
include exact existing exposure, proposed notional, capital, and applicable cap in the existing
verdict detail. No historical decision or baseline is rewritten.

Protective cancellations are not abandoned entries in the decision journal. A book still holding
inventory remains `holding`, with canceled/replacement protection linked in `orders[]`; an actual
entry cancellation keeps its existing classification. Journal changes never alter execution.

## Alternatives considered

- Raise allocations or loosen the published caps: rejected; neither corrects the scope mismatch.
- Sum bot allocations as account capital: rejected; allocations need not be backed by available
  account quote and can duplicate capital.
- Use available quote alone: rejected; buying managed inventory or reserving a buy entry would
  shrink the capital denominator while the same assets still occupy exposure.
- Infer missing historical baselines or replay blocked historical entries: rejected; preserve
  evidence and fail-closed behavior.

## Consequences

No migration, API shape, CLI flag, arming, lifecycle, or risk-policy document change. Ops contract
v63 remains valid. Regression tests cover protective maintenance versus entry cancellation, fresh
HTTP live starts, zero and unknown baselines, account versus allocation caps, stopped residuals,
partial buy reservations, short proceeds, unknown balances, and unchanged absolute loss caps.
