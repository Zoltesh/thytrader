# Fee-tier suggested defaults for research and paper (2026-09-13)

**Status: planned / not shipped.** Fee-tier *visibility* is already shipped (Phase 1:
`exchanges/fees.py`, `GET /api/v1/fees`, dashboard fee panel). This plan adds using that
tier as an **editable suggested default** for maker/taker rates in backtests and paper.

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

## Shipped today

- Read-only fee tier + 30-day volume + freshness/`as_of` on the dashboard.
- Research runs require explicit `maker_fee_rate` / `taker_fee_rate` in the immutable
  research-run / backtest submission contract.
- Result UI may project `CostAssumptions` and must not label them as observed Coinbase fees
  (`docs/architecture/backtest-simulation.md`).

## Planned behavior

1. **Prefill only** — Research (and paper cost fields if exposed) open with maker/taker
   derived from the latest fee-tier snapshot mapped through Coinbase's published schedule.
2. **Always editable** — operator may set custom rates; custom wins for that submission.
3. **Fingerprint what was used** — published runs store the exact rates submitted, not a
   live pointer to "current tier."
4. **Honest labels** — UI shows e.g. "Suggested from Coinbase fee tier (as of …)" vs
   "Custom." Never imply backtest/paper fees *are* Coinbase fills.
5. **Demo / missing credentials** — documented fallback (blank required fields or explicit
   placeholder rates); do not invent a fake tier.
6. **No silent mid-edit refresh** — if tier updates while a form is open, offer refresh or
   show stale-suggestion; do not overwrite in-progress edits without consent.
7. **Engine honesty** — V1 next-open fills remain taker-like in the bar contract even when
   the strategy prefers maker; defaults must not lie about which leg uses which rate.

## Implementation sketch (for Thy Builder)

- Map tier → maker/taker decimal rates from a versioned, tested schedule table (`as_of`).
- API: suggestion endpoint or extend fees payload with `suggested_maker_fee_rate` /
  `suggested_taker_fee_rate` + source metadata (tier id, schedule version, fetched_at).
- Thin web: Research (and paper if applicable) prefill + override + source chip.
- Docs/skills: update research skill when shipped; keep shipped vs planned split until then.

## Priority

Small UX increment. May ship in parallel with Phase 7 timeframe work, or immediately after
the next 15m dataset increment. See `docs/roadmap.md` Phase 7.1.

## Exit gate

With credentials, Research shows suggested rates from tier with override; without credentials,
fail closed to an honest fallback; every submitted run fingerprints the rates used; UI copy
never claims observed Coinbase fills for research/paper costs.
