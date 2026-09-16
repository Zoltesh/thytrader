# Fee-tier suggested defaults for research and paper (2026-09-13)

**Status: shipped (research prefill and paper deploy fields).** Fee-tier *visibility* was already
shipped (Phase 1: `exchanges/fees.py`, `GET /api/v1/fees`, dashboard fee panel). Research launch
fields prefill maker/taker from the operator's Coinbase fee-tier snapshot mapped through a
versioned schedule. Paper deploy and new paper discretionary books persist Decimal maker/taker
assumptions ([ADR 0048](../decisions/0048-paper-deploy-fee-fields.md)). Omitted paper rates keep
the documented `0.001` / `0.002` fill schedule. Live venue billing is unchanged.

## Intent

Operators often do not know their exact maker/taker bps. Research forms should prefill
credible defaults from the operator's Coinbase fee tier while always allowing manual
override. This supports vision principle **explicit assumptions** without pretending
modeled fees are live venue fills.

## Non-goals

- Changing live Coinbase fee billing (live always pays real venue fees).
- Silently mutating published research-run fingerprints when the tier changes.
- Presenting suggested rates as "observed Coinbase fees" on result screens.
- Blocking research when credentials/demo mode cannot fetch a tier.

## Shipped

- Read-only fee tier + 30-day volume + freshness/`as_of` on the dashboard.
- Research runs require explicit `maker_fee_rate` / `taker_fee_rate` in the immutable
  research-run / backtest submission contract. Submitted rates are fingerprinted; the run
  does not store a live pointer to the current Coinbase tier.
- Result UI may project `CostAssumptions` and must not label them as observed Coinbase fees
  (`docs/architecture/backtest-simulation.md`).
- Versioned schedule `coinbase-advanced-spot-fees-v1` (`as_of` 2026-09-13) in
  `exchanges/fee_schedule.py`. Live `GET /api/v1/fees` adds `suggested_maker_fee_rate` /
  `suggested_taker_fee_rate` plus source metadata (Coinbase tier id, schedule version,
  `fetched_at`). Demo or missing credentials keep the dashboard snapshot but set
  `suggestion_source=unavailable` (`demo_or_missing_credentials`) with null suggested rates.
- Research launch prefills those suggested rates when present, keeps fields editable, labels
  Suggested vs Custom vs stale, and never overwrites in-progress edits. Blank required fields
  when the suggestion is unavailable. V1/V2 copy states next-open fills use the taker rate
  even when the strategy prefers maker.
- Paper deploy and new paper discretionary books persist `maker_fee_rate` / `taker_fee_rate`.
  Omitted rates keep the documented `0.001` maker / `0.002` taker schedule. Live rejects the
  fields. Copy never claims observed Coinbase fees on paper fills.

## Remaining / out of scope

- Changing live Coinbase fee billing.
- Silently mutating published fingerprints when the tier changes.
- Claiming observed Coinbase fees on result screens.

## Exit gate

Met for research: credentials → suggested rates with override; demo/missing → honest blank
fallback; submitted runs fingerprint the rates used; UI copy never claims observed Coinbase
fills for research/paper costs.
