# 0144: Fee tiers come from Coinbase; no pinned schedule

- Status: Accepted
- Date: 2026-10-15
- Supersedes: the pinned schedule context of
  [0090](0090-research-correctness-optional-take-profit-diagnostics.md)
- Relates to: [0122](0122-paper-fees-default-to-account-rates.md)

## Context

ThyTrader shipped a hard-coded copy of Coinbase's public spot fee bands (captured 2026-09-13) and
reported the band for the account's volume beside the account's own rates, "as context". The copy
was wrong for real accounts: an Intro account billed 0.50% / 0.90% was shown a 0.40% / 0.60% band,
and the next band as 0.25% / 0.40% where Coinbase's next tier (Advanced 1) is 0.35% / 0.75%.
Research that reasoned about the next tier from that table reached the wrong conclusion.

Coinbase publishes no endpoint for the whole schedule, but the account's
`GET /api/v3/brokerage/transaction_summary` already returns, beside the billed `fee_tier`,
a `fee_tier_without_promotion` ladder: the current tier and the next tier (rates and the spot
volume and asset ranges that qualify), how the account qualifies, its current value and the next
tier's threshold.

## Decision

1. **No pinned schedule.** The hard-coded bands and the `schedule_*` / `suggestion_schedule_*`
   fields are removed from the fee report (HTTP, operator report, browser).
2. **The tier ladder comes from the source.** The fee report carries `current_tier`, `next_tier`,
   `qualification`, `qualification_value` and `next_tier_threshold` exactly as Coinbase reports
   them for this account. They are read best effort: a missing or malformed ladder leaves them
   null and never fails the fee read.
3. **Suggestions are unchanged.** Research and paper prefills stay the account's billed rates
   (ADR 0090, 0122). The next tier's rates are a labelled what-if for research only.

## Consequences

- When Coinbase changes its tiers, ThyTrader shows the change without a release.
- Only the next tier is known; a question about tiers beyond it needs Coinbase's own pages.
- Ops contract `v104`; the browser's fee hint names the next tier and the account's progress.
