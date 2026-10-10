# 0127: Read-only Coinbase CFM futures account mirror

- Status: Accepted
- Date: 2026-10-10
- Relates to: [0006](0006-credential-permission-acceptance.md),
  [0106](0106-account-risk-capital-and-live-startup-baselines.md),
  [0114](0114-readiness-preflight-and-venue-reconciliation.md),
  [0119](0119-venue-order-observation-provenance.md),
  [0126](0126-futures-instrument-catalog-read-only.md)

## Context

ADR 0126 made the futures catalog, funding history and candles observable. The account side
(margin, buying power, positions) lives in a separate Coinbase Financial Markets (CFM) account
that the Advanced Trade API exposes under `/api/v3/brokerage/cfm/*`. It is read with the same
View-scoped key ThyTrader already holds; there is no futures-specific permission. The same key
can also trade, sweep funds and change the intraday margin setting, so the read path must be
structurally unable to do any of that.

Facts that shape the design:

- The balance summary reports amounts as `{value, currency}` objects in USD, including
  `cbi_usd_balance` (spot-account USD that CFM pulls as margin), `cfm_usd_balance`,
  `available_margin`, `liquidation_threshold`, `funding_pnl` and two margin-window measures.
- Positions report `number_of_contracts` (contracts, not base units) and an RFC 3339
  `expiration_time`; there is no per-position liquidation price.
- `current_margin_window` requires `margin_profile_type`; the documented values are
  `MARGIN_PROFILE_TYPE_UNSPECIFIED`, `..._RETAIL_REGULAR` and `..._RETAIL_INTRADAY_MARGIN_1`.
- Coinbase documents no specific response for an account without futures access; only a
  generic error schema.

## Decision

### 1. A GET-only adapter

`exchanges/coinbase_cfm.py::CoinbaseCfmAccount` receives a transport typed with `get` only and
checks every request against a four-path allowlist before it is sent:
`cfm/balance_summary`, `cfm/positions`, `cfm/intraday/margin_setting` and
`cfm/intraday/current_margin_window` (with `margin_profile_type=MARGIN_PROFILE_TYPE_RETAIL_REGULAR`).
It never calls `orders`, `orders/preview` (the fee probe belongs to slice P1-3),
`close_position`, sweeps or the margin-setting POST. Tests pin the allowlist, the public method
set and the absence of any write call in the module.

### 2. Parsing that never guesses

Every amount is an exact `Decimal`; an absent or empty value is `None` (unknown), never zero. An
amount labelled with a currency other than USD fails the read, so it can never be summed with
USD downstream. One malformed field fails its whole read. Failures carry a short reason token
(`http_401`, `transport`, `malformed`, `non_usd_amount`, `path_not_allowlisted`), never venue
text or URLs.

### 3. Enablement

`enabled` requires a parsed balance summary. Every failed or unparseable balance read is
`unknown`. `not_enabled` exists in the model but is produced only for a documented venue
response proving the account has no futures access; Coinbase documents none today, so it is
not produced.

### 4. The mirror

`worker/futures_mirror.py` runs in the worker process every 60 seconds when Coinbase
credentials are configured (credential reloads rebuild the reader). Each cycle performs the four
reads independently: a failed read leaves its value unknown and is recorded as
`operation:reason`. The websocket `futures_balance_summary` channel waits for P2.

### 5. Persistence (Alembic 0072)

- `futures_account_snapshots`: one row per cycle. It holds every balance-summary amount as an
  exact USD string, both margin-window measures as canonical JSON of exact strings, the
  intraday margin setting, the margin window type and end, the killswitch flags, enablement,
  failed reads, and `position_count` (`NULL` when the position read failed).
- `futures_position_snapshots`: that cycle's positions, keyed by snapshot and product.
- The tables mirror venue state; the downgrade drops them, since the next read can observe the
  same state again.

### 6. Operator surface

`thytrader-operator futures-account` (`GET /api/v1/operator/futures-account`) reports the newest
snapshot with `orderable: false` and reason codes `OK`, `FUTURES_MIRROR_NOT_RUN`,
`FUTURES_MIRROR_STALE` (older than 180 s), `FUTURES_ACCOUNT_UNKNOWN`, `FUTURES_READ_FAILURES`,
`STORE_DISABLED` and `STORE_UNAVAILABLE`. Ops contract v79 adds `account_mirror` to
`futures_observations`. Readiness, venue reconciliation and the Home card are slice P0-6.

### 7. Currency scope

CFM amounts are USD and are never added to a USDC or USDT amount. `cbi_usd_balance` is reported
as a fact; it is not added to the spot portfolio totals, which keep USD, USDC and USDT separate.
Futures account capital (`cbi_usd_balance + cfm_usd_balance`, one read) and the margin ratio
(`available_margin / liquidation_threshold`) are derived by readers, never stored as sums.

### 8. Observed fact: USDC is CFM collateral (2026-10-10)

A live mirror snapshot of a futures-enabled account that held its spot cash as USDC and had
no CFM USD balance read `enablement: enabled`, every read succeeded, no positions, and
`futures_buying_power` equal to its USDC spot balance while `cbi_usd_balance` and
`cfm_usd_balance` were near zero. Futures buying power far exceeds the USD balances, so
**Coinbase counts the USDC spot balance as CFM futures collateral**. The intraday and overnight
margin-window types read `..._UNSPECIFIED` while the account is flat.

This overturns the plan's assumption (open question 2) that USDC cannot margin CFM, and
widens its shared-collateral rule, which covered only USD-quoted spot books:

- Futures margin and **USDC** spot books draw on one collateral pool. A futures loss or margin
  call can consume USDC that a spot book's allocation, risk capital (ADR 0106) and exposure caps
  assume is available.
- Every amount stays in its own currency: USD futures figures are never added to USDC. Reports
  state the sharing in words (`collateral_note`) instead of producing a combined number.
- `futures_buying_power` is not additional capital and must never be added to the USDC balance.
- P0-6 surfaces this in readiness (`FUTURES_COLLATERAL_SHARED`), venue reconciliation (external
  CFM positions as unmanaged exposure) and the Home futures card. ADRs 0128 and 0129 (P1) design
  the risk rules around it.

### 9. Readiness, venue reconciliation and Home (P0-6)

- Readiness adds a `futures` section from the newest mirror snapshot with a `margin_ratio`
  (`available_margin / liquidation_threshold`, `null` on a flat account) and findings
  `FUTURES_COLLATERAL_SHARED` (info), `FUTURES_POSITIONS_EXTERNAL` (advisory) and
  `FUTURES_ACCOUNT_UNKNOWN` (unknown).
- Venue reconciliation adds external CFM positions (USD notional = contracts x contract size x
  current price, when the contract size is listed) and nonterminal futures orders from a fresh
  read-only `orders/historical/batch?product_type=FUTURE` listing. All are
  `external_unmanaged`: ThyTrader manages no futures. Unknown evidence is an unknown finding.
- Home shows a read-only futures card (buying power, margin ratio, liquidation buffer, funding
  PnL) that states buying power is shared with the USDC spot balance.

### 10. History and same-cycle spot collateral (2026-10-10)

Before ThyTrader gets a live futures order path, the operator places one manual 1-contract
ETP short in the Coinbase app and closes it, and the lead measures from the mirror: how CFM
draws on USDC collateral, the commission, funding against our calculation, the
`liquidation_threshold` against initial margin (maintenance calibration), and the 16:00 ET
margin step-up.

- **Spot balances in the mirror cycle.** Spot USDC and USD were already recorded
  historically, but only inside the portfolio worker's snapshot JSON every 300 s, read at
  other instants than the CFM snapshots. That cannot show whether USDC available drops,
  converts or is held at the moment margin is posted. Each mirror cycle therefore also reads
  the complete spot account listing (the existing read-only `CoinbaseAccount.list_balances`,
  not the CFM adapter, whose four-path allowlist is unchanged) and stores
  `spot_usdc_available`, `spot_usdc_hold`, `spot_usd_available` and `spot_usd_hold` on the
  snapshot (Alembic 0074). A currency the complete listing omits is zero; a failed listing is
  unknown (`NULL`) with a `spot_balances:<reason>` read failure. The reads are sequential, not
  one atomic venue snapshot. USDC and USD stay separate figures.
- **History report.** `thytrader-operator futures-account --history --since ISO [--until ISO]`
  (`GET /api/v1/operator/futures-account/history`, `futures_account_history`) returns every
  snapshot in `[since, until)`, oldest first, at most 2880 rows (48 hours) per report with
  `truncated` paging, the gaps longer than three cycles (window edges included) and the
  margin-window changes. Each row carries the full balance summary with both margin-window
  measures (initial and maintenance margin), positions with contracts, side and average entry,
  the margin window type and the spot balances. `futures-account` gains `spot_collateral`.
- **Cadence stays 60 s.** Each quantity to be measured changes in steps at discrete events (a
  fill, an hourly funding settlement, the 16:00 ET window change). One-minute resolution puts a
  snapshot on each side of every step as long as the manual steps are a couple of minutes
  apart, and a faster cycle would multiply venue reads without adding evidence.
- **Visibility.** Missing evidence is never silent: gaps, rows with failed reads, rows without
  spot balances, an empty window and truncation each degrade the report with a reason code.

Ops contract v90 adds `account_mirror_history` to `futures_observations`.

## Consequences

- The account's futures state is observable and auditable from the day this is deployed,
  including failures.
- No new permission is needed, and no code path can change the futures account.
- One snapshot per minute is about 525,000 rows a year; a retention policy can follow now that
  the history report reads them (§10).
- An account without futures access shows `unknown` with its read failures, not `not_enabled`.

## Alternatives considered

- **Reuse the signed broker transport directly.** Rejected: its type exposes `post`, so a
  mistake could reach a write endpoint. The adapter's own `get`-only protocol keeps that out of
  reach by type.
- **Infer `not_enabled` from a 401, 403 or 404.** Rejected: those also mean a bad key, a scope
  change or a routing fault, and the venue documents none of them for this case.
- **Call `orders/preview` for fees now.** Rejected for P0. It is a POST and belongs to P1-3.
