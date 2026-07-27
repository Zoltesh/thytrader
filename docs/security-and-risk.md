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

### Pre-trade

- maximum order quantity and notional;
- maximum position and portfolio exposure;
- available-balance reserve;
- product allowlist;
- reference-price collar;
- maximum open orders;
- duplicate/idempotency protection;
- minimum liquidity and maximum spread;
- post-only enforcement for normal maker orders.

### Runtime

- daily realized/unrealized loss limit;
- per-strategy drawdown limit;
- order and cancellation rate limits;
- consecutive error/rejection circuit breaker;
- stale-market-data cutoff;
- API/WebSocket disconnect cutoff;
- heartbeat and clock-skew monitoring;
- exchange reconciliation;
- per-strategy and global kill switches.

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

The first distributable operator skill should be read-only. Agents may inspect health, configuration validity, risk state, data freshness, strategy performance, and redacted diagnostics. Live trading, configuration mutation, order cancellation, arming, or kill-switch operations require separate explicit tools and user confirmation policies.
