# Security and Trading-Risk Baseline

ThyTrader can place irreversible financial orders. Security and execution safety are product behavior, not optional hardening work.

## Credential rules

- Coinbase credentials remain server-side.
- `.env.example` contains variable names and placeholders only.
- Logs, exceptions, diagnostics, tests, fixtures, and agent output must redact secrets.
- View + Trade is sufficient for planned trading features, but ThyTrader accepts operator-selected
  keys with additional permissions.
- Inspect and report key permissions when Coinbase supports it; additional permissions never block
  connection by themselves.
- Enforce safety at application capabilities, live-arming, risk, and confirmation boundaries rather
  than inferring operator intent from the key's permission set.
- Never persist a private key in browser storage or send it over the UI API.
- In-app operator chat stores a user-pasted LLM API key in the API process only. It is not a
  Coinbase credential. Status and transcripts must not echo it; logs must redact it.

For the initial local deployment, `.env` is acceptable. A future hosted or multi-user product requires encrypted per-user secret storage and a new threat model.

## Network-access modes

### Default

- Bind application services to `127.0.0.1`.
- No login is required for a loopback-only initial installation.
- Refuse unsafe non-loopback startup unless an explicit protected mode is configured.

### Private VM access

- SSH port forwarding is the initial zero-configuration secure path.
- A future guided Tailscale profile may provide convenient private remote access.

### Public exposure

Never enabled automatically. It requires TLS, authentication, secure sessions/cookies, CSRF protection, rate limiting, and explicit operator acknowledgement. Public exposure needs a dedicated threat-model review.

## Live-trading controls

Live execution is disabled by default and must be explicitly armed. Arming displays the active product allowlist, notional/position limits, and loss limits. A fresh installation starts with conservative values.

Disarming blocks new risk-increasing orders. Emergency exits and cancellation behavior must be defined separately so a kill switch does not accidentally trap an open position.

## Baseline risk-policy registry

Risk controls are composable, independently testable policies with typed configuration and stable reason codes.

### Shipped (Phase 10)

`thytrader-risk-policy-v1` is the named Phase 10 registry ([ADR 0033](decisions/0033-phase-10-risk-policy-registry.md),
extended by [ADR 0050](decisions/0050-daily-loss-drawdown-rate-collars.md)).
It gates paper and live **entries** (not exits) with:

- product allowlist (empty means no extra restriction);
- concurrent running-deployment and open-position caps per mode (paused occupies a running slot;
  open, pending-entry, and pending-exit occupy an open slot);
- portfolio and per-product exposure fractions of the mode capital base;
- paper book `paper_capital_quote` and optional per-strategy `allocations`;
- UTC-day daily-loss fraction of the mode capital base;
- per-strategy fill-ledger drawdown fraction;
- rolling 60-second entry-order and cancellation caps;
- last-close reference-price collar for priced risk-increasing orders.

Compiled default when no published row is active: eight running slots and eight open positions per
mode, unit exposure and breaker fractions, 60 orders/cancels per minute, collar `0.5`, empty
allowlist/allocations, paper book `100000`. Operator `risk` reports `available` and omits account
balances (fractions and integers are allowed). Discretionary entries use this same registry;
nonempty allocations deny them.

### Shipped execution and runtime controls

These are already product behavior (not destination remainders):

- product allowlist (Phase 10; empty means no extra restriction);
- portfolio and per-product exposure fractions of the mode capital base;
- daily realized plus unrealized loss limit (UTC day; pauses the mode);
- per-strategy fill-ledger drawdown limit (pauses that book);
- order and cancellation rate limits (rolling 60s; deny without pause);
- reference-price collar versus last close (deny without pause);
- duplicate/idempotency protection (unique client order IDs; persist intent before submit;
  reconcile ambiguous timeouts before retry);
- post-only enforcement for normal maker entries and ordinary take-profit exits;
- stale-market-data cutoff (block new risk-increasing orders on stale data or unhealthy required
  connections);
- exchange reconciliation on restart and after ambiguous submit.

The lists below keep remaining destination controls visible; they do **not** re-list the shipped
controls above.

### Pre-trade (destination remainders)

- maximum order quantity and notional beyond the shipped exposure fractions;
- available-balance reserve;
- minimum liquidity and maximum spread.

### Runtime (destination remainders)

- consecutive error/rejection circuit breaker;
- heartbeat and clock-skew monitoring beyond existing worker supervision;
- per-strategy and global kill switches beyond pause/stop (disarming vs trapped-position behavior).

## Execution policy

- Prefer post-only maker entries and normal take-profit exits.
- Capital protection outranks maker fees for emergency stops.
- Stop exits may be taker or otherwise marketable when required.
- Coinbase-native brackets/stop orders may be used where their semantics match the strategy.
- Synthetic trailing stops require continuously persisted state and a healthy worker/data feed.

A stop-limit order can remain unfilled in a fast market. The UI and strategy schema must distinguish guaranteed execution intent from price-limited execution.

## Idempotency and reconciliation

- Generate a unique client order ID for every intent.
- Persist intent before attempting submission.
- A timeout does not prove that Coinbase rejected the order.
- Query and reconcile ambiguous state before retrying.
- On restart, reconcile balances, open orders, recent fills, and local state before resuming strategies.
- Pause affected strategies when a safe conclusion cannot be reached.

## Audit and observability

Record append-oriented events for:

- strategy version and lifecycle changes;
- live arm/disarm actions;
- market-data health transitions;
- signals and relevant input references;
- risk approvals, resizing, and rejections;
- order intents, submissions, acknowledgements, fills, and cancellations;
- reconciliation decisions;
- synthetic stop updates and triggers;
- operator and future agent actions.

Audit data must be useful without exposing credentials or unnecessary personal/account data.

## Agent safety boundary

The agent surface is the **primary product** ([ADR 0030](decisions/0030-agent-e2e-primary-surface.md)).
That does not relax confirmation, live-arming, or skill-lane rules.

The first distributable operator skill is read-only. Agents may inspect health, configuration
validity, risk state, data freshness, strategy performance, and redacted diagnostics through
`thytrader-operator`. Live trading, configuration mutation, order cancellation, arming, or
kill-switch operations require separate explicit tools and user confirmation policies. Research
mutations use `thytrader-research` with `--confirm`. On-demand trades use the same order-intent
and risk boundary as strategy-driven orders; they never call Coinbase directly from a skill
([ADR 0039](decisions/0039-on-demand-discretionary-trades.md),
[ADR 0046](decisions/0046-shipped-vs-remaining-0031-destination.md)).
