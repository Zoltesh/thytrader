# 0125: Correlation-aware risk limits

- Status: Accepted
- Date: 2026-10-09
- Relates to: [0033](0033-phase-10-risk-policy-registry.md),
  [0063](0063-stage-5-release-discipline-ci-risk-defaults-rate-budget.md),
  [0091](0091-portfolio-deployment-limits-and-manager-proposals.md),
  [0106](0106-account-risk-capital-and-live-startup-baselines.md),
  [0120](0120-verified-risk-opening-evidence.md),
  [0124](0124-inventory-adoption.md)

## Context

The risk policy caps total exposure and per-product exposure as if every product moved
independently. A fleet of long bots on many USDC alt markets is not independent: most alts
move with BTC at a beta above 1. For example, a 1,000 USDC total cap on alts with beta near 1.2
is roughly 1,200 USDC of BTC-equivalent exposure, so one 30% BTC drawdown would cost more than a
third of a 1,000 USDC account before stops fire.

The fleet also enters in bursts. The 2h, 4h, 6h and 1d bars all close together at 00:00
UTC, so many bots can fire on one bar close and build the whole correlated bet within
minutes. The per-minute entry-rate cap does not see this: it limits order mutations, not how
many books open risk at the same event.

## Decision

The policy gains two independent, **opt-in** limits. Each is a set of optional fields on
`RiskPolicyDefinition` and `RiskPolicyWrite`, excluded from the canonical document while
unset. An unset field means exactly today's behaviour, and existing policies keep their
fingerprints. Neither limit needs a migration: the policy is stored as canonical JSON text
and reason codes have no CHECK constraint.

### 1. Fleet entry clustering cap

- **Fields.** `max_fleet_entries_per_window` (1–128) and `fleet_entry_window_minutes`
  (1–1440), set together or not at all: a window alone would change the fingerprint without
  changing behaviour.
- **Count.** The distinct `(deployment, product)` pairs with an `ENTRY`-purpose intent
  created in `[as_of − window, as_of]`, across every book of the entry's mode, stopped books
  included, so a bot that entered and was then stopped still counts. `ADOPTION` intents are
  not entries and never count. A new entry is denied with `FLEET_ENTRY_CLUSTER_LIMIT` when
  the count has reached the cap. The detail names the count, the window, the cap, the oldest
  counted entry and when a slot frees.
- **Scope.**
  - Strategy entries, portfolio sleeves and discretionary entries are gated.
  - Pyramid adds are gated and counted.
  - A reprice of an already-admitted working entry is not gated (its admission happened when
    the entry was first placed). Its replacement intent is an ordinary `ENTRY` intent, so it
    still counts toward other books' windows; this errs toward denying.
  - In-kind adoption (ADR 0124) is not gated: nothing reaches the venue.
- **Evidence.** The check runs in the entry breaker stage beside the rate and collar gates.
  When the cap is set and the gate has no observation (no `as_of`), it denies.

### 2. BTC-beta-weighted exposure cap

- **Fields.** `max_btc_beta_exposure_fraction` (a unit fraction of the capital base) and
  `max_btc_beta_exposure_quote` (an absolute quote ceiling, live-only, like
  `max_portfolio_exposure_quote`). Either may be set alone; when both are set the tighter
  wins.
- **Measure.** Σ |product exposure| × β over the same-quote books the exposure gate already
  uses (`trading.exposure.product_exposure`: position cost plus working entries), plus the
  proposed notional × the proposed product's β. Exposure is counted gross: a short never
  hedges a long. β is floored at 0. Capital is the existing capital base, including an
  in-kind adoption's notional. A breach denies with `BTC_BETA_EXPOSURE_EXCEEDED`.
- **Reference.** `BTC-<quote>` of the entry's quote, so BTC-USDC for a USDC book. Quotes are
  never mixed: only the books the exposure gate already scopes to the entry's quote are
  summed. The reference product itself has β = 1 by definition.
- **Estimator.** Ordinary least-squares β of daily log returns of the product on the
  reference over the last 90 settled UTC daily bars, aligned by bar start. A return whose two
  bars are not consecutive days is dropped. At least 60 returns are required. The float
  result is converted to `Decimal`, rounded up (ceiling) to 0.01, and clamped to [0, 3].
  Floats are acceptable here because β is an analytic estimate, not an exchange quantity.
- **Evidence source.** `MarketDataService.get_range` on the 1d interval over 91 bars, one
  Coinbase page. It is the same read-only historical path the risk layer already uses for
  midnight marks (`risk/accounting_evidence.py`). Not the research dataset catalog, which is
  analytics and not an operational source of truth. Not the deploy-anchored execution
  window, whose start depends on the deploy anchor rather than a fixed lookback.
- **Cache.** An in-process cache keyed by product, reference and the UTC daily close. It
  recomputes once per UTC day after the daily bar settles. A failed fetch keeps the previous
  estimate.
- **Fail closed.** An estimate is unavailable when history is insufficient, every fetch has
  failed, or its last bar closed more than 48 hours before the entry's `as_of`. If the
  proposed product or any held same-quote product has no available β, the entry is denied
  with `BTC_BETA_UNAVAILABLE`, naming the product and the cause. Unknown evidence denies
  new risk; a conservative default β would silently admit a newly listed coin. The 48-hour
  tolerance absorbs a one-day fetch outage. An operator who wants to trade a product with
  too little history removes it from the allowlist or unsets the cap.
- **Scope.** Every entry the gate admits, pyramid adds and reprices included (a reprice is
  re-checked on its remaining notional). In-kind adoption is subject to it, consistent with
  the exposure caps. The β loader runs only when a β cap binds in the entry's mode (the
  absolute cap alone does not bind paper), so an unset cap reads nothing.
- **Wiring.** Worker entries (strategy, lockstep, portfolio sleeves, reprices) read through
  the market data the execution worker binds for risk evidence. Discretionary place-order and
  both adoption paths pass their own market data. With a cap set and no market data, the entry
  is denied as `not_loaded`.

### Shared rules

- **Protective exits are never gated.** Neither limit is reachable from protective,
  cancellation or exit paths: they live inside `evaluate_new_entry` only.
- **Not a breaker.** Neither reason code pauses a book (`pauses_risk_increasing` is
  unchanged). The signal is skipped for that bar and recorded in the decision journal and
  why-trade journal like any other entry denial.
- **Paper and live** both apply the limits. Paper capital is `paper_capital_quote`; the
  absolute beta cap is live-only.
- **Backtests** never call the account gate, so neither limit is simulated. Fleet backtests
  overstate trade counts while the clustering cap binds, and paper/live twin comparisons can
  diverge from them.
- **Contract.** Each limit bumps the ops contract when it ships (the risk-policy registry
  contract changes), and the operator `risk` payload reports the new fields.

### Caveats

- **Processing order is arbitrary.** Which bots win a clustered bar depends on worker order:
  `list_deployments` sorted by `updated_at DESC`, which favours recently updated bots. A later
  ADR could add a deterministic priority.
- **Concurrent admission can overshoot.** The API and the worker can admit at the same time
  and exceed the clustering count by one, exactly as the existing rate caps can.

### Deferred: correlation clusters

Grouping products into clusters by pairwise correlation (for example ρ > 0.7) with a cap per
cluster is deferred. In crypto, BTC's first principal component dominates, so at a useful
threshold the USDC markets in this fleet fall into roughly one cluster and the cap becomes a
noisier copy of the beta cap. It also needs O(n²) pairwise estimates, and products near the
threshold flip membership, so denials would not be monotonic in exposure. Revisit it only
when assets with low BTC beta join the fleet.

## Consequences

- An operator can bound the standing BTC-equivalent bet and the speed it builds, separately,
  without changing any existing limit. Recommended starting values for a small fleet of long alt bots:
  `max_btc_beta_exposure_fraction = 0.6` with no absolute beta cap, and
  `max_fleet_entries_per_window = 4` with `fleet_entry_window_minutes = 120`.
- With the beta cap set, a product whose daily history cannot be read blocks new entries
  fleet-wide in that quote while it is held. This is intended: the sum is unknown.
- The worker issues about one daily-candle request per traded product per UTC day.
- **Rollout.** This ADR first; then the pure β estimator; then the clustering cap end to end
  (ops contract v73); then the β evidence loader; then the beta cap end to end (v74). Each
  slice is inert until an operator publishes the new fields.

## Alternatives considered

- **Correlation clusters.** Deferred, as above.
- **A conservative default β for missing history.** It admits new coins silently, the case
  most likely to be wrong. Rejected for fail-closed evidence.
- **β from the research dataset catalog (Parquet).** Research data is not an operational
  source of truth and may be stale or absent on a live host. Rejected.
- **β from each bot's execution window.** The window is anchored at the deploy, so its
  lookback varies by bot and by timeframe. Rejected for one fixed daily lookback.
- **Net exposure (shorts hedge longs).** A short and a long on two products are only a hedge
  if their betas are measured and stable; counting gross is the safe side. Rejected.
- **Counting clustering by orders instead of intents.** Orders include protective and
  replacement work and adoption records. Intents carry the purpose. Rejected.
- **Putting the clustering check in `risk/breakers.py`.** That module holds the loss
  breakers, rates and collars and is near its size budget. A separate module keeps one
  concern per module.
