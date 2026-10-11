# Live futures policy preparation (P2-3)

This surface publishes **policy only**, not trading authority. All live futures start
paths still refuse `FUTURES_LIVE_UNSUPPORTED` until P2-7 (ADR 0134). There is no supported
live futures start command in this slice. Do not probe order endpoints or bypass the refusal.

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
| `--futures-live-derisk-margin-ratio R` | `live_derisk_margin_ratio` | Decimal in (1, 100], future margin-monitor threshold on available margin / liquidation threshold. Persisted now; monitor wiring is P2-4. |
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
Unset policy fields preserve older policy fingerprints.

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

The worker does not yet supply this gate's live venue evidence. Live futures fleet-health,
alerts, monitoring and start wiring remain later slices. Do not interpret a stored opt-in,
a healthy spot report, or a passing policy validation as live futures readiness.
