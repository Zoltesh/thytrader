# 0142: Start checks warn by default; refusing is an opt-in safeguard

- Status: Accepted
- Date: 2026-10-15
- Supersedes: the refusal defaults of [0140](0140-indicator-convergence-warmup.md) decisions 6
  and 8
- Relates to: [0063](0063-stage-5-release-discipline-ci-risk-defaults-rate-budget.md),
  [0082](0082-strategy-root-mutable-strategies-auto-snapshots.md)

## Context

ADR 0140 made paper and live starts and operator resumes refuse a strategy snapshot saved before
the convergence warmup policy, and starts refuse a market younger than the strategy's warmup.
ThyTrader is an open-source application that gives its user control: someone who upgrades, or
downloads it and asks an agent to start trading, would find every strategy saved before the
upgrade refused until they re-save each one, and a start on a young market refused outright.
The checks are worth running and reporting; whether they should block is the user's choice.

## Decision

1. **Starts adopt the warmup policy.** Before a start (single bot, adoption start, portfolio
   sleeve) snapshots a strategy saved before the policy, the server re-saves its unchanged
   document, which is exactly what any save does: the policy is adopted and the warmups raised,
   the strategy's revision and current fingerprint move on, and the start snapshots that. A
   revision race or a convergence warmup above the declarable 10,000 bars leaves it as saved.
2. **Checks warn.** A snapshot that still lacks the policy starts or resumes with a
   `WARMUP_POLICY_MISSING` warning; a market younger than the warmup starts with
   `HISTORY_TOO_SHORT` (or `HISTORY_UNCONFIRMED` when unreadable), and the bot skips entries until
   each window holds its warmup. Start and resume responses carry `warnings`; portfolio outcomes
   carry them in their message; the browser appends them to the accepted-action notice.
3. **Refusal is opt-in.** The risk policy's optional `start_safeguards` block turns a warning into
   a refusal: `require_convergence_warmup` (409 `WARMUP_POLICY_REQUIRED`, portfolio problem
   `warmup_policy_required`) and `require_market_history` (409 `HISTORY_TOO_SHORT` /
   `HISTORY_UNCONFIRMED`, problems `history_too_short` / `history_unconfirmed`). Both are off by
   default and omitted from the policy's canonical bytes while unset, so existing policy
   fingerprints are unchanged.
4. **Correctness guards stay on.** The worker's refusal to enter on a window shorter than its
   warmup is not a restriction on the user but part of evaluating the strategy as written; it is
   unaffected. Reference watches must still cover their warmup, since a reference series without
   a watch is not ingested at all.

New safeguards follow the same rule: run the check, report it, and let the operator choose to
enforce it.

## Consequences

- No strategy is unusable after an upgrade; existing paused bots resume with a warning.
- Starting an older strategy changes its saved document and fingerprint, as saving would; its
  earlier backtests describe the old warmup.
- Operators who want the strict behaviour set it once in the risk policy.
