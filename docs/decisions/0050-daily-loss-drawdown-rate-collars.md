# 0050: Daily-loss, drawdown, order-rate, and reference-price collars

- Status: Accepted
- Date: 2026-09-16
- Relates to: [0004](0004-safe-execution-and-access.md),
  [0019](0019-ops-contract-identity.md),
  [0033](0033-phase-10-risk-policy-registry.md),
  [0039](0039-on-demand-discretionary-trades.md),
  [0045](0045-spot-shorting-and-attached-entry-brackets.md),
  [0046](0046-shipped-vs-remaining-0031-destination.md),
  [0047](0047-wider-fail-closed-indicator-catalog.md),
  [0048](0048-paper-deploy-fee-fields.md),
  [0049](0049-experiential-train-v1.md)

## Context

Phase 10 shipped a typed `thytrader-risk-policy-v1` registry: allowlist, concurrent slots,
exposure fractions, paper book, and allocations ([ADR 0033](0033-phase-10-risk-policy-registry.md)).
Entries already go through that gate; exits do not. Destination still named daily-loss and
drawdown breakers, order-rate limits, and reference-price collars
([docs/security-and-risk.md](../security-and-risk.md),
[ADR 0046](0046-shipped-vs-remaining-0031-destination.md) remaining list).

Those remainders were not pause-on-breaker behavior. `--confirm` and `--i-understand-live` stay
untouched. Extra exchanges stay out. This slice does not rewrite
[ADR 0047](0047-wider-fail-closed-indicator-catalog.md) (indicator catalog),
[ADR 0048](0048-paper-deploy-fee-fields.md) (paper deploy fees, ops v16 / Alembic `0028`), or
[ADR 0049](0049-experiential-train-v1.md) (experiential trainer, ops v17 / Alembic `0029`).

## Decision

Keep schema `thytrader-risk-policy-v1`. Add breaker, rate, and collar fields to the same
immutable document. Overlay compiled defaults when stored JSON omits them so Phase 10 rows still
load. Fingerprint of a stored row remains SHA-256 of the stored bytes; GET returns the effective
overlaid limits.

Compiled defaults are a permissive envelope so existing paper `price=close` tests and live bid
versus last close stay inside the collar: daily-loss and drawdown fractions `"1"`,
`max_entry_orders_per_minute` / `max_cancellations_per_minute` `60`,
`reference_price_collar_fraction` `"0.5"`.

### Daily-loss and drawdown (pause)

- Daily loss is UTC-day realized PnL since midnight plus current unrealized, summed across
  occupied books in that paper or live mode, versus the same capital base as exposure.
- Drawdown is this strategy's fill-ledger maximum drawdown fraction.
- A trip **denies** further risk-increasing orders and **pauses**: daily-loss pauses every running
  book in that mode; drawdown pauses this book. Exits on paused books continue.
- Missing last-close marks on open inventory deny with `BREAKER_MARK_MISSING` and do **not** pause.
- Resume HTTP is not blocked; the next closed bar re-pauses while the limit still holds.

### Rate limits and collars (deny without pause)

- Rolling 60-second caps count existing orders and cancellations in occupied books of that mode,
  not the proposed order.
- Priced entries require `|limit − last_close| / last_close` at or under the collar.
- Marketable entries (no proposed price) still require a positive last close and skip the
  deviation check.
- Rate and collar denies skip the bar / return HTTP conflict; they do not pause.

`evaluate_new_deployment` is unchanged. Start is not a risk-increasing order until an entry.

Alembic `0030` revises `0029` and records the overlay as a table comment on
`published_risk_policies`. It does **not** rewrite stored canonical JSON or
fingerprints, and it does not touch ADR 0048 paper-fee columns or ADR 0049
`experiential_models`. Ops contract becomes `thytrader-ops-contract-v18` with
`risk_breakers` (`daily_loss`, `drawdown`), `order_rate_limits` (`entry`, `cancel`),
and `reference_price_collars` (`paper`, `live`), keeping ADR 0048
`paper_deploy_fee_fields`, ADR 0049 `experiential_model_engines`, and
`expected_schema_revision` `0030`.

`--confirm` on `set-risk-policy` remains a hard gate. Live start and live place-order still
require `--i-understand-live`. Coinbase Advanced Trade spot only.

## Consequences

- Operators publish tighter breakers through existing `thytrader-runtime set-risk-policy --confirm`.
- Operator `risk` reports the effective fractions and integers (no dollar PnL) and prefixes pause
  details with `DAILY_LOSS_LIMIT` / `STRATEGY_DRAWDOWN_LIMIT`.
- A stale Compose image that still advertises v17 / Alembic `0029` fails the
  ops-contract preflight (`make run`).
- Intra-strategy pyramiding, multi-instrument documents, extra exchanges, consecutive-error
  breakers, and max order qty/notional beyond exposure fractions stay out.

This **supersedes in part** ADR 0046's remaining-destination list for daily-loss / drawdown
breakers, order-rate limits, and reference-price collars only. It does not rewrite ADR 0046,
ADR 0047, ADR 0048, or ADR 0049.

## Alternatives considered

- **New `thytrader-risk-policy-v2` schema:** rejected; overlay omitted keys so published v1 rows
  keep loading.
- **Pause on rate or collar:** rejected; those are fat-finger / churn guards, not portfolio kills.
- **Fail-open when a sibling mark is missing:** rejected; inventing equity is forbidden.
- **Block resume HTTP after a trip:** rejected; operators may resume; the next bar re-evaluates.
- **Tighten the compiled collar below 0.5:** rejected; paper maker `price=close` and live bid
  versus last close must remain admissible until an operator publishes a tighter fraction.
- **Extra exchanges in this slice:** rejected; Coinbase Advanced Trade spot only.
- **Claim ADR 0049 / ops v17 / Alembic `0029`:** rejected; those numbers belong to the
  experiential trainer. Health `expected_schema_revision` must match the applied HEAD
  revision after `make run`.
