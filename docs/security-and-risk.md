# Security and Trading-Risk Baseline

ThyTrader can place irreversible financial orders. Security and execution safety are product behavior, not optional hardening work.

Account risk capital is separate from bot allocations ([ADR 0106](decisions/0106-account-risk-capital-and-live-startup-baselines.md)).
Live exposure and daily-loss fractions use one observed venue quote balance plus managed long
inventory cost and remaining quote reserved by buy entries, restricted to the proposed spot quote.
Other quote inventories never enlarge that loss/exposure denominator. Ledger cash, duplicated balances,
short proceeds, and protective exits do not inflate that base. Allocations still cap strategy
sizing/exposure; portfolio caps and published absolute limits remain unchanged. Unknown venue
balances and missing marks/baselines deny entries. New live strategy ledgers start at exact zero,
which is valid baseline evidence, not missing data.

Strategy drawdown uses pinned performance capital separately from that account capital
([ADR 0107](decisions/0107-capital-normalized-live-performance.md)). A zero-based live ledger
therefore counts losses before its first profit. The budget and worst observed drawdown survive
restart/rebalance. The breaker compares current loss from the durable peak; unknown positive
capital or incomplete marks deny new risk. Explicit latch reset does not erase the peak or
observed maximum and does not resume the bot. Cash, recorded fees, and UTC-day loss retain their
existing accounting meanings.

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

Live execution is disabled by default and must be explicitly armed. Arming displays the active product allowlist, notional/position limits, and loss limits. The compiled fallback policy is a wide **paper research** envelope, not a conservative live default: a fresh, credentialed installation cannot start a live deployment or on-demand live order until the operator publishes an explicit risk policy (`LIVE_REQUIRES_PUBLISHED_POLICY`; [ADR 0063](decisions/0063-stage-5-release-discipline-ci-risk-defaults-rate-budget.md)). Paper starts are unaffected. This repository does not assert a universal "safe" loss or exposure percentage; the operator sets fractions (and, optionally, absolute quote caps below) that fit their own capital and tested strategy.

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
- paper book `paper_capital_quote` and optional per-strategy `allocations` (allocation
  membership gates **live** only; a listed strategy's paper starting cash and exposure stay
  bounded by its allocation, while unlisted paper strategies and paper discretionary books are
  allowed);
- UTC-day daily-loss fraction of the mode capital base, plus an optional absolute
  `max_daily_loss_quote` ceiling (live only) enforced at whichever bound is tighter.
  The loss sum includes stopped flat books of that mode and the same spot quote,
  including fills dated today after stop. Stop is not a reset. USD, USDC, and USDT
  are never added together. An open book without a same-UTC-day baseline, or a book
  whose quote cannot be read, denies with `BREAKER_MARK_MISSING`
  ([ADR 0111](decisions/0111-durable-risk-accounting-scopes.md)). Strategy deletion retains stopped
  paper/live books, fills, latches, and snapshots. Flat books with no current-day fills contribute
  zero after rollover; day fills must prove flat per-product inventory at midnight unless a
  current-day opening equity exists. Overnight closure without opening marks and unapplied live
  economics deny rather than guessing day PnL. UTC rollover never auto-resets a latch;
- per-strategy fill-ledger drawdown fraction. A drawdown latch or breach blocks only
  that strategy, or a discretionary book on the same product. It does not block
  unrelated strategies in the mode;
- rolling 60-second entry-order and cancellation caps, purpose-aware since ADR 0063: only
  `ENTRY`-purpose orders consume the entry cap, so protective (stop/take-profit/time-exit/bracket)
  submissions never exhaust it and deny an unrelated new entry;
- an optional combined `max_venue_order_actions_per_minute` budget across entries and cancellations
  together, denying only new entries when the venue's overall recent request volume is high;
- last-close reference-price collar for priced risk-increasing orders;
- an optional absolute `max_portfolio_exposure_quote` ceiling (live only) alongside the portfolio
  exposure fraction.

Exposure counts inventory and working entry remainders; reserved buying power counts the entry
remainders alone. Orders with
verified non-entry intent purposes (take-profit, stop, time exit, signal exit, or bracket) do not
reserve entry quote, including paper limit exits. Missing intent evidence remains conservatively
occupied; venue-native protective kinds remain excluded ([ADR 0058](decisions/0058-protection-lifecycle-accounting.md),
[ADR 0091](decisions/0091-portfolio-deployment-limits-and-manager-proposals.md)).

Compiled default when no published row is active: eight running slots and eight open positions per
mode, unit exposure and breaker fractions, 60 orders/cancels per minute, collar `0.5`, empty
allowlist/allocations, paper book `100000`, and no absolute caps or venue budget set. This default
is a wide **paper** research envelope; a published policy is required before it can arm live
(above). Operator `risk` reports `available` and omits account balances (fractions and integers are
allowed). Discretionary entries use this same registry; nonempty allocations deny live discretionary
entries (paper discretionary tickets stay allowed).

### Shipped execution and runtime controls

These are already product behavior (not destination remainders):

- product allowlist (Phase 10; empty means no extra restriction);
- portfolio and per-product exposure fractions of the mode capital base;
- daily equity-change loss limit (UTC day; pauses running books in that mode and quote, preserving
  stopped and deliberate-pause choices);
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

- minimum liquidity and maximum spread.

Optional `max_order_quantity`, `max_order_notional_quote`, and
`min_available_quote_reserve` are entry bounds on the same policy document. They are
unset in the compiled default and in stored documents that omit them, so publishing
nothing new does not tighten current limits. When an operator sets one, only the
entry that exceeds it is denied (`MAX_ORDER_QUANTITY`, `MAX_ORDER_NOTIONAL`,
`BALANCE_RESERVE`). New monetary bounds require the proposed quote to match the
policy's `quote_currency`. Reserve is **notional admission headroom, not a guaranteed
post-fill balance**. Live uses observed available quote minus candidate notional and
local unheld buy remainders; confirmed venue holds are not subtracted twice and
ambiguous holds deny. Live fees/slippage are unknown and not guaranteed covered.
Paper includes occupied-book cash changes (recorded fees/loss), working buys, and
conservative stored/default taker fees. Unknown quantity while capped, live quote,
or paper opening cash fails closed. These checks are not atomic venue reservations
against simultaneous external trading. Protective exits never use these entry gates.

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
Live books whose strategy declares `take_profit: {"kind": "none"}` are protected by one Coinbase
stop-limit triggered at the working stop with its limit 5% through it — the same offset Coinbase
applies to the stop leg of a TP/SL bracket, so their gap risk equals every other live book's
([ADR 0090](decisions/0090-research-correctness-optional-take-profit-diagnostics.md)). The worker
never invents a take-profit price to obtain a bracket.

A strategy's `exits.signal_exit` rule ([ADR 0093](decisions/0093-signal-based-exits.md)) is a
risk-reducing marketable exit: it pays the taker fee, it never replaces the mandatory initial stop,
and it keeps running while a book is paused or under managed shutdown. Live cancels the book's
protection before the cover, so the position is unprotected between an accepted cancel and the
sell. A durable position marker keeps every later cycle exiting instead of re-resting protection,
and the cover follows as soon as the venue confirms the cancel.

## Idempotency and reconciliation

- Generate a unique client order ID for every intent.
- Persist intent before attempting submission.
- A timeout does not prove that Coinbase rejected the order.
- Query and reconcile ambiguous state before retrying.
- On restart, reconcile balances, open orders, recent fills, and local state before resuming strategies.
- Pause affected strategies when a safe conclusion cannot be reached.

Venue order-state observation time is persisted independently of local accounting updates
([ADR 0119](decisions/0119-venue-order-observation-provenance.md)). Only a successful identified
live order read supplies it; unknown results invalidate it. A migration, a local write, or a
restart does not manufacture a fresh venue observation. Observation freshness does not prove
venue geometry, complete account reconciliation, or guaranteed stop-limit execution.

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

A missing newest decision candle receives only ADR 0104's absolute 120-second publication wait
when prior history and the persisted evaluation cursor are contiguous. New entries remain
blocked; reconciliation and protection continue. The deadline survives restart. Older gaps,
required-clock/feed failures, operator pauses, and breakers keep their fail-closed behavior.
