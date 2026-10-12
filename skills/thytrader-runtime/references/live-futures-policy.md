# Live futures policy preparation and dormant runtime (P2-2–P2-4)

This surface publishes **policy only**, not trading authority. All live futures start
paths still refuse `FUTURES_LIVE_UNSUPPORTED` until P2-7 (ADR 0134). There is no supported
live futures start command in this slice. Do not probe order endpoints or bypass the refusal.

## Dormant adapter is not a supported order path

P2-2 adds internal futures request and product-routing adapters, exercised only with fake
transports. The default worker/API wiring and all operator commands remain spot-only; `futures_order_paths`
remains empty. Do not import an adapter as an operating workaround or treat a passing fake
venue test as proof of Coinbase acceptance. No real probe or policy change is authorized by
this implementation.

The two constructor-only reduction candidates are sized `close_position` (planned default)
and market IOC with `reduce_only: true`. There is no automatic fallback, plain market exit
or market entry. Post-only entries, both reduction modes and standalone venue protection
still need supervised acceptance/enforcement evidence before live wiring; attached entry
brackets remain disabled. P2-4 tests injected runtime composition with product exclusivity and
position checks; it is not enabled in production. Opaque order ids are resolved from bounded venue reads, not a memory cache;
incomplete identity/fill evidence fails closed after restart as well. Routing a known venue
id does not require an attached spot child's local bookkeeping label to be echoed. Client-only
scans require exact client matching and valid identities on every row/page; malformed nonmatches
cannot prove absence or uniqueness and cannot authorize cancellation. No clientless scan-row
representation is supported. Futures fill timestamps must have a known explicit RFC3339 offset
and an exactly representable UTC instant. Local clock fields are restricted to hours 00–23,
minutes 00–59 and seconds 00–59 before parsing; hour 24 is rejected, never normalized to the
next day. Invalid offsets, UTC overflow and sub-microsecond precision loss are domain errors,
not silently normalized evidence.

Internal pre-request refusals are definite REJECTED with no broker transport sends:
`FUTURES_BROKER_UNAVAILABLE`, `FUTURES_PRODUCT_UNSUPPORTED`, `FUTURES_INTENT_INVALID`,
`FUTURES_ORDER_KIND_UNSUPPORTED`, `FUTURES_PRICE_INVALID`, `FUTURES_ATTACHMENT_UNVERIFIED`,
`FUTURES_CONTRACT_SIZE_UNKNOWN`, and `FUTURES_FRACTIONAL_CONTRACTS`. They are not new
operator report fields or permission to retry through another path. Financial quantities
must convert exactly between whole contracts and base units. Fill fees use the all-in
`commission`, never the rounded `commission_detail_total.total_commission`.

## Read, confirm, replace, verify

1. Read `uv run thytrader-runtime show-risk-policy` and `uv run thytrader-operator risk`.
2. Explain the exact replacement policy and obtain the operator's confirmation before
   `set-risk-policy --confirm`. Preserve every existing optional field that should remain:
   publication replaces the whole policy, not a patch. Omitting a field unsets it.
3. Add the flags below to the complete `set-risk-policy` invocation. Use operator-approved
   values and catalog product IDs, not example policy amounts as recommendations.
4. Read `show-risk-policy` again and check all values and the new fingerprint. The read-only
   operator `risk` report echoes the same settings under `payload.futures`.

The HTTP equivalent is `PUT /api/v1/risk-policy`, with these keys nested under `futures`.
Use JSON decimal strings, a JSON boolean for `live_enabled`, and an array for `product_allowlist`.
Existing authentication/CSRF rules still apply. Policy publication never arms trading.

| CLI flag | Nested field | Meaning / validation |
|---|---|---|
| `--futures-live-enabled true\|false` | `live_enabled` | Explicit opt-in; unset or false denies live futures risk. False is retained, not omitted. |
| `--futures-live-capital-usd N` | `live_capital_usd` | Positive USD allocation envelope and basis for futures-scope daily loss/exposure; not buying power or spot capital. |
| `--futures-product-allowlist PRODUCT` (repeatable) | `product_allowlist` | Up to 32 unique `CODE-DDMONYY-CDE` IDs. Live requires a nonempty list. A nonempty list also restricts paper futures. Spot uses the separate top-level allowlist. |
| `--futures-live-derisk-margin-ratio R` | `live_derisk_margin_ratio` | Decimal in (1, 100], margin-monitor threshold on available margin / liquidation threshold. Exercised in the injected P2-4 runtime only; production activation is still refused. |
| `--futures-live-funding-drift-tolerance-usd N` | `live_funding_drift_tolerance_usd` | Positive USD tolerance. Persisted now; alert wiring is P2-6. |
| `--futures-max-order-contracts N` | `max_order_contracts` | Existing positive integer cap: live defaults to one contract when unset, for both the order and resulting position. Paper keeps its existing unset behavior. |

The existing `--futures-live-spot-collateral-reserve-quote` and `--futures-peg-haircut`
remain necessary for future live admission: reserve must cover **(current + proposed initial
margin) × haircut**. Reserve is in the policy quote (USD or USDC only for live futures);
margin is USD. This is the declared-peg threshold comparison, never a currency sum.
Size reserve with headroom for margin changes. USDT is not recognized as CFM collateral.

## Gate contract, not runtime readiness

The pure gate requires opt-in, capital, allowlist, an exclusive perp-style product and
fresh UTC venue evidence (at most 180 seconds old; future reads rejected). Positions,
external nonterminal orders, buying power, initial margin, killswitch and catalog contract
size must be known. A recorded pending/current maintenance window blocks admission.
Unset policy fields preserve older policy fingerprints. The pure live gate denies if its
contract counts, allocation totals or margin calculations overflow, underflow, become nonfinite,
or discard nonzero digits at the active Decimal precision. Such values cannot certify capacity;
only rounding trailing zeroes is harmless. Order count failures use
`FUTURES_ORDER_CONTRACTS_EXCEEDED`, unrepresentable held positions or proposed margin use
`FUTURES_COLLATERAL_UNKNOWN`, allocation totals use `FUTURES_LIVE_CAPITAL_EXCEEDED`, and
projected reserve failures use `FUTURES_COLLATERAL_RESERVE_SHORT`. These are controlled admission
denials even with caller arithmetic traps disabled, not permission to loosen policy or restart.

Denial codes include `FUTURES_LIVE_DISABLED`, `FUTURES_LIVE_CAPITAL_UNSET`,
`FUTURES_LIVE_CAPITAL_EXCEEDED`, `PRODUCT_NOT_ALLOWLISTED`, `FUTURES_PRODUCT_OCCUPIED`,
`FUTURES_EXTERNAL_POSITION_ON_PRODUCT`, `FUTURES_LIVE_DATED_UNSUPPORTED`,
`FUTURES_ORDER_CONTRACTS_EXCEEDED`, `FUTURES_COLLATERAL_UNKNOWN`,
`FUTURES_BUYING_POWER_SHORT`, `FUTURES_COLLATERAL_RESERVE_SHORT`,
`FUTURES_VENUE_KILLSWITCH`, `FUTURES_VENUE_MAINTENANCE`, and `FUTURES_CONTRACT_DRIFT`.
They deny admission, not protective exits. Managed same-mode futures and USD/USDC spot daily-loss
breakers are logically linked (`SHARED_COLLATERAL_BREAKER`), without adding their losses.
Live beta netting remains disabled. Futures book equity for margin is allocation plus ledger
equity; paper stays ledger equity. Live allocation must be finite and positive even when a
separate performance-capital basis is pinned. Unknown/nonfinite equity denies new entries with
`BREAKER_MARK_MISSING`; a valid performance basis cannot substitute for margin capital.

The futures daily-loss fraction applies to `live_capital_usd` (or `paper_capital_usd` for
paper), with the optional `--futures-max-daily-loss-usd` absolute ceiling. An unset futures
ceiling does **not** inherit the spot `--max-daily-loss-quote`, even for a USD spot policy.
Spot USD and CFM USD are distinct settlement scopes. Linked latches still deny across the
collateral group; they do not import another scope's limit.

## Injected runtime is not operational readiness (P2-4)

The runtime can now prepare an existing synthetic live book, read fresh CFM balance/position
facts, use futures fees and whole-contract sizing, reconcile signed positions, accrue funding,
and cancel-confirm before explicit reductions. It is exercised with fake transports through the
real worker, broker adapter and atomic memory/PostgreSQL stores. Production venue construction
still supplies **no futures runtime**. There is no command to activate or bypass that boundary.

Future operational semantics are pinned, not enabled here:
- An exact accepted futures cancel remains pending if its confirmation GET fails. Restarts use
  the persisted marker and GET-only reconciliation; acceptance is not terminal cancellation.
  Malformed acknowledgements are uncertain, never manufactured acceptance or definite rejection.
- A validated strategy without a take-profit submits standalone STOP-purpose stop-limit protection,
  including paused partial-fill replacements. Target-enabled protection retains BRACKET purpose.
  Definite protection rejections retain the existing backoff latch; spot identities are unchanged.
- Two distinct fresh valid position mismatches pause the book. Unknown or reused evidence cannot
  manufacture confirmation. Confirmed unfilled resting protection does not suppress comparison;
  uncertain/in-flight transactions and fills do. Restart rechecks; it never clears a pause.
- An intent-only crash restores an UNKNOWN local observation anchor with the original client id
  for reconciliation. It never resubmits, even if the venue scan finds no order. Stopped-flat books
  with unresolved intents remain supervised; absence is not evidence that a retry is safe.
- Orphan or displaced managed protection is canceled only with independent complete exclusive
  signed fill-ledger ownership proof. Cancellation precedes strict position comparison when a
  partial OPEN stop would block it; matching open-book protection stays resting. A flip/excess is
  reduced only after cancellation/race-fill reconciliation and two fresh valid mismatches;
  unrelated inventory is untouched. The book stays paused after repair. No operator repair command is added.
- Margin de-risk continues under entry disarm, managed stop and live-policy disablement. A breach
  is stored independently as `futures_derisk_pending` before any cancellation and remains pending
  through unrelated reconciliation faults, restart and cancel/submit uncertainty, even if margin
  recovers. A filled de-risk
  pauses entries for review. Stops must be confirmed canceled before any reduction is sent.
- Futures flatten uses an explicit price-free reduction without inventing a candle. Unknown
  position/quantity/ownership still blocks it. All live futures closes, including natural protective
  fills during managed shutdown and closes applied before a stop command, wait for a subsequent
  fresh venue-flat observation before retirement. `futures_flat_confirmation_after` is the durable
  lower bound, not a successful observation timestamp. It is persisted before cancellation or
  submission and advanced atomically with canonical fill accounting. A later distinct complete
  observation must be strictly after the last fill plus five seconds. Exactly five seconds is
  insufficient. Future fills, including supported timestamps near the upper UTC date limit, keep
  cancellation and position proofs unknown without aborting supervision or discarding canonical
  fill economics; the pending duty remains. This is an elapsed-interval check, not a clamped date.
  A margin-monitor message
  cannot overwrite this duty. Read both fields with `thytrader-runtime show` or operator `runtime`;
  never clear them by database edits. Alembic 0076 and ops contract v95 are required (ADR 0135).
  This does not change spot/paper flatten semantics.
- Funding is hourly **modeled accrual at the settled rate**, not confirmed venue settlement;
  missing/nonfinite evidence is never filled with zero. USD figures are not summed with USDC.

Venue enforcement/acceptance probes, the protection decision (P2-5), live futures fleet-health
and alerts (P2-6), and acknowledgements/start activation (P2-7) remain outstanding. Do not interpret
a stored opt-in, a healthy spot report or a passing fake-runtime test as live futures readiness.
