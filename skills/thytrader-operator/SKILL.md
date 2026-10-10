---
name: thytrader-operator
description: >-
  Diagnose a running ThyTrader instance through the versioned read-only operator
  CLI and HTTP API. Use when checking health, configuration, Coinbase connectivity,
  market-data freshness, strategy/runtime status, backtest or paper/live performance,
  reconciliation, or a redacted support bundle. Never places, edits, or cancels
  orders and never arms live trading. `chat-status` reports whether an in-app LLM
  key is held in the API process; it never prints the key and is not Coinbase.
---

# ThyTrader operator

Read-only diagnostics for a running instance. Do not scrape logs, query PostgreSQL, or import private internals.

Portfolio exposure counts inventory cost plus working entry remainders. Verified protective and
other exit intents, including paper limit exits, do not add entry exposure. Orders without intent
evidence stay conservatively counted. The portfolio manager briefing marks open books from the
same decision journal as bot detail, with verified entry fees and net PnL when available.

Schema version: `thytrader-operator-report-v1` (`schema_version` on every JSON report).

Default transport is the loopback HTTP API; base-URL resolution, installation auth, failure
messages, and the stale-image rule are in the [shared rules](../README.md#shared-rules-every-lane).
Pass `--local` only when you intentionally want process stores instead of HTTP. Do not fall back
from HTTP to PostgreSQL if the API is down. Read-only operator routes stay unauthenticated
([ADR 0061](../../docs/decisions/0061-application-trust-boundary.md)).

JSON is the default CLI output. Do not add `--format json` to every command.

## Hard stop

When operating a running instance, do not edit `src/`, `compose.yaml`, Dockerfiles, Alembic, or
tests, and do not search the tree for a code patch. Report failures through this skill. Rebuild
only with `make run` when the user asked or the [stale-image rule](../README.md#shared-rules-every-lane)
applies. Run every `uv run thytrader-*` command from the repository root (the parent of `ops/`).

## Commands

Prefer the CLI. HTTP is the same contract on loopback.

| Need | CLI | HTTP |
|---|---|---|
| Health | `uv run thytrader-operator health` | `GET /api/v1/operator/health` |
| Configuration | `uv run thytrader-operator configuration` | `GET /api/v1/operator/configuration` |
| Exchange | `uv run thytrader-operator exchange` | `GET /api/v1/operator/exchange` |
| Market data | `uv run thytrader-operator market-data [--product-id BTC-USD\|BIP-20DEC30-CDE] [--timeframe 1h\|5m\|15m\|30m\|6h\|1d\|1m\|2h\|4h]` | `GET /api/v1/operator/market-data` |
| Data catalog | `uv run thytrader-operator data-catalog` | `GET /api/v1/operator/data-catalog` |
| All watched tails | `uv run thytrader-operator data-health` | `GET /api/v1/operator/data-health` |
| Products | `uv run thytrader-operator products [--kind spot\|future\|all]` | `GET /api/v1/operator/products[?kind=future\|all]` (default `spot` is the enabled spot catalog, unchanged; `future` lists the read-only Coinbase CFM futures contracts instead, `all` lists both; futures rows are `orderable: false`; [ADR 0126](../../docs/decisions/0126-futures-instrument-catalog-read-only.md)) |
| Futures account (read-only) | `uv run thytrader-operator futures-account` | `GET /api/v1/operator/futures-account` (latest CFM mirror snapshot the worker records every 60 s with GET-only reads: `enablement`, `read_failures`, USD balance summary, positions in contracts, margin window and setting; `orderable: false`; never summed with spot USDC; [ADR 0127](../../docs/decisions/0127-cfm-futures-account-mirror.md)) |
| Paper futures books (read-only) | `uv run thytrader-operator futures-books` | `GET /api/v1/operator/futures-books` (every paper futures book: bound contract, side and contracts, mark, USD equity, notional, leverage, overnight initial/maintenance margin, liquidation buffer vs the policy minimum, liquidation price, funding ledger, `entry_blocks` and `unknown` evidence, plus the `futures.paper_capital_usd` envelope; one bot: `GET /api/v1/deployments/{id}/futures`; [ADR 0129](../../docs/decisions/0129-paper-futures-books-and-shared-collateral-risk.md)) |
| Futures funding history | `uv run thytrader-operator funding [--product-id BIP-20DEC30-CDE] [--hours 1..720]` | `GET /api/v1/operator/funding` (read-only Coinbase CFM perp funding recorded by the market-data worker every 5 minutes; poller health, per-contract coverage, gaps and conflicts; `--product-id` adds every stored hour; futures cannot be ordered; [ADR 0126](../../docs/decisions/0126-futures-instrument-catalog-read-only.md)) |
| Indicators | `uv run thytrader-operator indicators` | `GET /api/v1/operator/indicators` |
| Strategies / runtimes | `uv run thytrader-operator strategies` | `GET /api/v1/operator/strategies` |
| Runtime watch | `uv run thytrader-operator runtime [--deployment-id UUID]` | `GET /api/v1/operator/runtime` (component `execution_market_data` / `DEMO_MARKET_DATA` when Coinbase credentials are absent and paper books evaluate synthetic demo candles) |
| Monitor | `uv run thytrader-operator monitor` | `GET /api/v1/operator/monitor` (deployments, recent journals, notify delivery; omits balances and webhook URLs) |
| Safety alerts | `uv run thytrader-operator alerts` | `GET /api/v1/operator/alerts` (durable pause/mismatch, breaker, uncovered or unknown stop cover, stop-triggered-but-unfilled, missed decision/maintenance deadlines, worker lease age including unknown, consecutive worker failures; local feed works with `notify_provider=none` and sets `delivery_warning`; no webhook URL; no order authority; ADR 0115) |
| Why-trade review | `uv run thytrader-operator trade-reasons [--intent-id UUID] [--deployment-id UUID]` | `GET /api/v1/operator/trade-reasons` |
| Decision timeline | `uv run thytrader-operator decisions [--deployment-id UUID \| --strategy-id UUID] [--outcome OUTCOME ...] [--limit N] [--cursor C]` | `GET /api/v1/operator/decisions` (per-bar `thytrader-bar-decision-v1` rows, newest first; repeated `outcome`; `next_cursor` paging) |
| Performance | `uv run thytrader-operator performance --result-fingerprint sha256:…` or `--deployment-id UUID` | `GET /api/v1/operator/performance` |
| Risk | `uv run thytrader-operator risk` | `GET /api/v1/operator/risk` (registry identity, slot counts, breaker fractions/ints, pause/mismatch; omits balances) |
| Reconciliation | `uv run thytrader-operator reconciliation` | `GET /api/v1/operator/reconciliation` (every paused `mismatch_detail` is a `STATE_MISMATCH` finding whose `detail` is the mismatch text; split pending-entry state adds `FILLED_WITHOUT_FILL` or `PENDING_ENTRY_WITHOUT_ENTRY`; a live order Coinbase reports FILLED with no List Fills rows (`Filled order has no REST fills.`) adds `FILLED_WITHOUT_FILL` next to `STATE_MISMATCH`; `unknown` orders add `UNKNOWN_ORDERS`; recent audit failures add `AUDIT_FAILURES`) |
| Studies | `uv run thytrader-operator studies` | `GET /api/v1/operator/studies` (persisted research-study catalog rows; omits child equity) |
| Portfolio | `uv run thytrader-operator portfolio` | `GET /api/v1/operator/portfolio` (balances with `balances_omitted=false`; never credentials; `totals` are exact per currency (USD, USDC, USDT) and never added together; `total_value` is only the labelled `usd_pegged_approximate` sum at 1:1, `null` when the account could not be read) |
| Fees | `uv run thytrader-operator fees` | `GET /api/v1/operator/fees` (fee tier plus suggested maker/taker = the account's reported Coinbase rates; `schedule_*` is context only; `payload.futures` is the separate futures fee tier; `fee_per_contract` is null unless you pass `--futures-preview-product-id CDE_ID`, which sends one Coinbase `orders/preview` POST for one contract (it places **no** order) and reports the quoted commission with `fee_per_contract_source: orders_preview`) |
| Portfolios | `uv run thytrader-operator portfolios` | `GET /api/v1/operator/portfolios` (sleeves, issues, allocation, limits, manager settings, `deployable`, `deployment_state`, `breaker_latched` / `breaker_reason_code`, `pending_proposals`, newest portfolio backtest, and `paper_live_fill_comparisons` for explicitly linked paper/live twins with verified identical trading rules; component `PORTFOLIO_BREAKER_LATCHED` when a breaker holds sleeves paused). Edit portfolios and act as the manager with `thytrader-portfolio`; start/stop them with `thytrader-runtime portfolio-*` (ADR 0088, ADR 0091) |
| Readiness preflight | `uv run thytrader-operator readiness [--deployment-id UUID] [--portfolio-id UUID]` | `GET /api/v1/operator/readiness` (advisory allocation vs venue quote vs account and portfolio caps, per-asset caps, remaining entry capacity, paper fee assumptions vs account fee evidence, and which daily-loss breaker binds tighter; never changes policy; [ADR 0114](../../docs/decisions/0114-readiness-preflight-and-venue-reconciliation.md)) |
| Venue reconciliation | `uv run thytrader-operator venue-reconciliation` | `GET /api/v1/operator/venue-reconciliation` (managed live inventory and working orders versus a fresh venue listing; foreign holdings are not errors and are not flattened; incomplete listings stay unknown; [ADR 0114](../../docs/decisions/0114-readiness-preflight-and-venue-reconciliation.md). To protect or sell `external_inventory`, a person uses the runtime lane's `adoption-preview`, `place-order --entry-kind adopt` and `sell-holdings`; [ADR 0124](../../docs/decisions/0124-inventory-adoption.md)) |
| Support bundle | `uv run thytrader-operator support-bundle` | `GET /api/v1/operator/support-bundle` |
| Schema check | `uv run thytrader-operator schema-check` | (local files only) |
| In-app LLM key flag | `uv run thytrader-operator chat-status` | `GET /api/v1/operator-chat/status` (HTTP-only; never prints the key; not Coinbase; `--local` is rejected) |
| Execution quality | `uv run thytrader-operator execution-quality --deployment-id UUID [--twin]` | `GET /api/v1/deployments/{id}/execution-quality` and `.../execution-quality/twin` (HTTP-only; recorded closed-trade fees and journaled-close slippage; missing fees/liquidity are not zero; `--local` is rejected; ADR 0116) |

`execution-quality` slippage is relative to the persisted intent's **completed** decision
bar; `reference_*` identifies that price, intent and bar. It is not a future fill-bar close
or a quote at later repricing. Each book's `recorded_fills` includes partial exits exactly
once. Twin `population=recorded_fill_lifetime` summaries are **context only** when
`summaries_context_only=true`; intersecting dates do not prove equal histories or rules.
Different strategy fingerprints need the server's pinned-rule proof (ADR 0105). Fee
normalization always covers all applied lifetime live fills except adoptions, independent of overlap;
unknown liquidity or missing fill coverage makes counterfactual fees and delta **null**,
never zero. Keep the observed fees and PnL separate from those assumptions. A fill with
`adopted: true` is an in-kind inventory adoption (ADR 0124): coins already held that a live
book took over at a mark. It opens its round trip but has no liquidity, slippage or fee to
re-price. `totals.adopted_fill_count` counts them, and they are left out of the slippage
fill counts and the twin fee normalization.

`--format text` is a short summary. Parent flags such as `--format` may follow the subcommand.

Machine-readable envelope: [operator-report-v1.schema.json](references/operator-report-v1.schema.json).

For `readiness`, inspect inventory read `status`, separate `accounting_status`,
`unresolved_deployment_ids`, and `partial_result_warnings` before reading remaining capacity.
Successful full reads can still contain unprojected applied inventory or unpublished/unapplied
executions (`BOOK_ACCOUNTING_UNRESOLVED`). Dependent exposure/capacity is null, not free capacity. Exposure is position cost plus working entries, not live
marks. Actual product quotes (not the deployment primary product) determine scope;
mixed books have per-quote rows and no cross-quote total. A deployment preflight still
counts portfolio siblings. Missing runtime state has a null latch, not a reset.
Paper/other-quote portfolio breakers are not compared to the live policy-quote account.
For `venue-reconciliation`, **both** `managed_listing` and venue listings must be complete
before foreign/orphan claims mean anything. Also inspect managed `accounting_status` and
`unresolved_deployment_ids`: `MANAGED_ACCOUNTING_UNRESOLVED` leaves affected asset quantities
and `foreign_quantity` null (`managed_unknown`), even if every read succeeded. Independent
asset/order comparisons can remain known; economic incompleteness is not a failed venue read. Missing storage is not an empty fleet.
Spot-history pagination includes queued cancellations/edits and pending orders; unknown
statuses or malformed/duplicate pages fail closed. `CANCEL_QUEUED` is not cancellation
confirmation. These observations never authorize replacement, cancellation, or flattening.

`strategies` lists the 100 most recently updated strategies (`strategy_id`, `name`, `revision`, `valid`,
`current_fingerprint` or `null` when the saved definition is invalid, `product_id`, `timeframe`,
`updated_at`) plus deployment rows; there are no drafts, publications, or versions. Deployment rows
carry `strategy_id` (null for a stopped live book of a deleted strategy), the snapshot
`strategy_fingerprint`, `strategy_name`, and `strategy_deleted`. A deployment whose
`strategy_fingerprint` differs from its strategy's `current_fingerprint` runs an earlier edit.
Read `partial_result_warnings`: an older deployed strategy can be absent from the bounded library
rows. Its absence does not mean deletion or a rule mismatch. Read its current rules with
`uv run thytrader-research show-strategy --strategy-id UUID`, or page the full library with
`thytrader-research list-strategies --limit 100` and the returned `--cursor`.

`strategies` and `runtime` deployment rows include redacted `books[]` (`product_id`, `phase`,
`side`, `protection_status`, `protection`, `position_state`, `exit_in_flight`). They omit prices,
cash, and order payloads. `protection` coverage quantities are the only sizes on the row
([ADR 0060](../../docs/decisions/0060-multi-book-deployment-api.md),
[ADR 0112](../../docs/decisions/0112-quantitative-protection-evidence.md)).
Each row also carries the deployment's worst-book `position_state` / `exit_in_flight`
([ADR 0097](../../docs/decisions/0097-runtime-parity-and-observability.md)). Report a book by
`position_state`, not by `phase`: `phase: pending_exit` includes an open book whose TP/SL
bracket (or stop-only protection) merely rests, which is `open_protected`. Only `exiting`
(`exit_in_flight: true`) means an exit is being sent.
`protection_status` is `flat` / `covered` / `unprotected` / `unknown` from matching persisted
stop evidence, not inferred parent geometry
([ADR 0058](../../docs/decisions/0058-protection-lifecycle-accounting.md)). An open paper book is
`covered` when its inventory economics are resolved (its synthetic stop runs every closed
bar), so it agrees with `position_state`
([ADR 0098](../../docs/decisions/0098-library-views-book-marks-portfolio-fills.md)). Do not read that
paper `covered` as a venue-resting stop. Each book also carries `protection`
([ADR 0112](../../docs/decisions/0112-quantitative-protection-evidence.md)): `required_quantity`,
`covered_quantity`, `uncovered_quantity` (exact decimals or null for unresolved inventory;
unknown is not zero and is never a sell quantity; these coverage quantities are the
exception to the no-quantity redaction and are not prices or cash), `stop_side`,
`stop_side_valid`, `stop_geometry_valid`, `mechanism` (`venue` / `synthetic` / `none` /
`unverified`), `venue_resting`, `worker_dependent`, `observed_at`, `verified_at` (null means
unknown — do not invent a time), and `reasons`. Live `covered` requires a recent OPEN stop on
the closing side whose remaining quantity and stop geometry match the book. A take-profit alone,
a pending stop, and an unknown stop are not covered. The same attached child is counted once.
`observation_source` names `venue_order_state`, `persisted_order` (legacy/local-only),
`synthetic_worker`, or `none`. Live timestamps come only from `Order.venue_observed_at`
([ADR 0119](../../docs/decisions/0119-venue-order-observation-provenance.md)), never local
`updated_at`: `observed_at` is the latest relevant receipt; `verified_at` is the oldest receipt
among contributing fresh OPEN stops. With partial cover it verifies only that fraction, not
the book. `freshness` (`recent_venue` / `stale` / `unknown`) conservatively describes order-state
age against `evaluated_at`, with `freshness_max_age_seconds: 120` (worker-poll based, not candle
frequency). Every contributing partial needs its own fresh receipt. Missing, stale, future-dated,
or unidentified OPEN rows contribute no covered quantity. UNKNOWN/error reads clear receipt
provenance; legacy rows stay unknown until actually observed. Local writes cannot refresh it.
`covered` plus `recent_venue` proves fresh order state matching **persisted submitted geometry**,
not an independent venue geometry audit or whole-account reconciliation. UI says **Order state
fresh** in amber and discloses the geometry limitation; do not report audited/guaranteed venue
cover or a green audit claim. Old `recent_local` payloads remain unverified.
`geometry_basis` is `working_target`, `stop_limit_trigger`, or `unknown`; profitable trailing
stops may cross entry. A STOP tag or trigger on a plain limit is not an executable stop.
Duplicate precedence uses real venue receipts, not local recency. Terminal/UNKNOWN histories,
missing provenance, geometry/quantity conflicts, and regressing fills cannot resurrect OPEN
coverage through another local write.
Paper `mechanism` is `synthetic` and `worker_dependent` is true. These are read-only reporting
rules: do not automatically pause/resume or replace orders based on them. Rows also include
`lifecycle_command`, breaker latches (`daily_loss_latched`, `drawdown_latched`), optimistic
`revision`, and `worker_lease_held` (boolean only; no holder identity). Latches persist across
pause and managed shutdown until an explicit operator reset via
`thytrader-runtime reset-breaker-latches UUID --confirm` /
`POST /api/v1/deployments/{id}/reset-breaker-latches` (always `--confirm`; YOLO never skips).
Pause still maintains protection;
it only blocks new entries and risk-up reprice. Default stop is managed shutdown; flatten is
explicit (`--flatten` / `?flatten=true`). Live capital (`allocated_capital`,
`venue_available_quote`) is on `thytrader-runtime show` / `GET /api/v1/deployments/{id}` — this
skill omits cash. A secondary open book is never implied by the deployment primary `product_id`.
For sizes, orders, and fills use `thytrader-runtime show` (`positions`, `instrument_runtimes`,
product-tagged orders/fills, `book_totals`). The singular HTTP `position` field is
compatibility-only.

Unprojected applied fills and unsettled executions report `unknown` / `open_unverified`, not
`flat`, even after restart or clearing a mismatch. Bounded summaries omit retained fill economics:
absent positions are unverified and aggregate `ledger_mark_complete` is false, not a flatness
certificate. Full `performance` reports set `ACCOUNTING_UNRESOLVED` and null dependent PnL/equity/
exposure when economics are unresolved; known recorded fill statistics remain population evidence.
Use `thytrader-runtime show UUID` (`detail=full`) for retained orders/fills, and `readiness` /
`venue-reconciliation` for completeness. A prior qualified opening or prior profits do not repair
projection. Protection/trigger incidents do not recover on missing positions or terminal status
until their product's economics are resolved; independently proved row checks may still recover.
For complete books, `ledger_mark_complete` requires every needed last-close mark. Focused valid
book protection/marks are local evidence, never complete shared-account accounting.

## Decision timeline

Every paper and live strategy bot journals one decision per completed bar and covered product
([ADR 0087](../../docs/decisions/0087-per-bar-decision-timeline.md)). Use it to answer "why did (or
didn't) this bot trade?" instead of reading logs:

```bash
uv run thytrader-operator decisions --deployment-id UUID
uv run thytrader-operator decisions --deployment-id UUID --outcome entry_blocked
uv run thytrader-operator decisions --strategy-id UUID --outcome entry_signal --outcome exit
```

Without a filter the report pages every bot newest first. Each row is a `thytrader-bar-decision-v1`
record (see [report-schemas.md](references/report-schemas.md)): `outcome` is one of
`entry_signal`, `no_signal`, `holding`, `exit`, `entry_blocked`, `skipped`, or `error`; `summary`
is a one-line reason such as `No trade: RSI(14) 47.21 needs ≥ 50`; `rule` holds the evaluated
entry tree (ALL/ANY/NOT plus each leaf's label, operator, both values rounded to 12 significant
digits — exact values stay in `rule.signal.indicator_values` — and `true`/`false`/`unknown`)
and the HTF filter (labels mark another clock as `[4h]` and combined declaration/operand offsets as `(1 bar ago)`;
the value is the lagged one the runtime compared); `risk` is the risk or freshness verdict; `action`, `intent_id`, `orders`, and
`fills` link what was sent; `skip_reason` (`cooldown`, `max_open_positions`, `warmup`,
`pending_entry`, `paused`, `stopped`, `data_gap`, `bar_settling`, `user_feed_gate`, `catch_up`,
`entries_disabled`, `entry_geometry`, `entry_sizing`, `reference_data_stale`,
`reference_data_missing`) and `exit_reason` (`stop`, `trail`,
`target`, `time`, `flatten`, `signal`) name the cause. `signal` is the strategy's
`exits.signal_exit` rule; those rows also carry `exit_rule` (its `outcome` and evaluated tree), as
does every post-fill holding bar of such a strategy
([ADR 0093](../../docs/decisions/0093-signal-based-exits.md)). `reference_data_stale` /
`reference_data_missing` mean a read-only reference instrument (for example a BTC 1d regime gate)
had no usable closed bar, so no entry was attempted and the bot kept running; `summary` names the
series, and reference operands are labeled like `BTC · EMA(100) [1d]`
([ADR 0096](../../docs/decisions/0096-reference-instruments.md)). `entry_geometry` / `entry_sizing` mark a matched
signal that rested no order; its `reason_code` is exact — `TARGET_NOT_POSITIVE` (a short's
take-profit would be at or below zero), `STOP_NOT_POSITIVE`, `STOP_DISTANCE_NOT_POSITIVE`,
`NOTIONAL_BELOW_MINIMUM`, `QUANTITY_BELOW_VENUE_MINIMUM`, `NOTIONAL_BELOW_VENUE_MINIMUM`,
`INSUFFICIENT_CASH`, or `SIZING_CASH_UNAVAILABLE`
([ADR 0090](../../docs/decisions/0090-research-correctness-optional-take-profit-diagnostics.md)). Values are exact Decimal strings. Pass `next_cursor` back as `--cursor` for older bars.
The journal keeps the newest 20,000 decisions per bot for at most 180 days. `storage: unavailable`
means the API has no database; it is not "no decisions". A bar is journaled only once it closes
and is processed, so the newest bar lags the clock by up to one execution-worker interval.

Populated HTTP timelines include `rule.signal.candle_starts_at` as a UTC timestamp alongside
the exact indicator values. The CLI validates the complete report, including nested signal
records; malformed, timezone-naive, or non-UTC signal timestamps remain schema errors. Report
such an error as a diagnostic failure, never as an empty timeline or a missing trading signal.

Protective bracket/TP cancellations and replacements keep an occupied book `holding`; inspect
linked `orders[].purpose` and statuses for maintenance. `pending_entry` cancellation refers only
to a known entry, including partial-entry remainder cancellation, never an attached protective
child. Historical rows retain what their worker originally reported.

Risk rejection details expose the exact existing exposure, proposed notional, capital, and cap.
Account live capital is observed quote plus managed long inventory cost and working buy-entry
quote; per-bot allocations are separate limits. `BREAKER_MARK_MISSING` names the deployment and
distinguishes missing inventory marks from unavailable equity/day-open baselines. A valid zero
live ledger baseline is not missing. Use the runtime lane's `show` and `show-risk-policy` for
capital and policy evidence; never infer that a flat bot or a healthy reconciliation permits
bypassing a risk denial ([ADR 0106](../../docs/decisions/0106-account-risk-capital-and-live-startup-baselines.md)).
The primary `risk` payload also echoes `max_order_quantity`, `max_order_notional_quote`, and
`min_available_quote_reserve` as nullable decimal strings: null means the optional bound is unset,
not that account funds are missing. These are configured limits, never observed balances.
Risk publication replaces the entire policy; omitting a previously configured bound removes it
from the successor, so read `show-risk-policy` and resupply bounds you intend to retain.
It also echoes `max_fleet_entries_per_window` and `fleet_entry_window_minutes` as nullable
integers: the opt-in fleet entry clustering cap, null when unset
([ADR 0125](../../docs/decisions/0125-correlation-aware-risk-limits.md)). A
`FLEET_ENTRY_CLUSTER_LIMIT` denial skips that bar's entry without pausing the bot; its detail
names the count, window, cap, and when a slot frees. It is a policy limit, not a fault.
`max_btc_beta_exposure_fraction` and `max_btc_beta_exposure_quote` (nullable decimal strings)
echo the opt-in BTC-beta-weighted exposure cap. `BTC_BETA_EXPOSURE_EXCEEDED` is a policy
limit. `BTC_BETA_UNAVAILABLE` means some product's daily history vs `BTC-<quote>` is short
(fewer than 60 returns), unreadable or over 48 h old, so new entries in that quote are blocked.
Its detail names the product and cause. The β history is read from the venue, not the research
dataset catalog. The remedy is a policy or allowlist change through the runtime lane, never a
bypass.
`futures` echoes the policy's futures block (`live_spot_collateral_reserve_quote`,
`peg_haircut`, `paper_capital_usd`, `daily_loss_limit_fraction`, `max_daily_loss_usd`,
`max_leverage`, `min_liquidation_buffer_fraction`, `max_exposure_fraction`,
`max_order_contracts`, `max_hourly_funding_rate_abs`, `max_btc_beta_exposure_fraction`,
`beta_netting`; each null or absent when unset) and `futures_collateral` shows the shared-USDC-collateral gate
([ADR 0129](../../docs/decisions/0129-paper-futures-books-and-shared-collateral-risk.md)):
`state` (`absent`, `idle`, `in_use`, `unknown`), `cause`, the CFM `initial_margin_usd`,
`open_orders_hold_usd` and `position_count`, the policy quote and reserve, and `effect` on new
live entries in the policy quote: `none`, `not_linked` (USDT), `live_spot_entries_denied`
(`FUTURES_COLLATERAL_UNKNOWN` or `FUTURES_COLLATERAL_IN_USE`), `reserve_short_entries_denied`
(`FUTURES_COLLATERAL_RESERVE_SHORT`) or `reserve_withheld`. Those codes are policy limits on
the shared pool, not faults; closing the manual futures or declaring a reserve through the
runtime lane resolves them.
Runtime `show` exposes `capital.risk_day_open_evidence` separately from preserved legacy
`utc_day_open_equity`; a legacy stamp or an old evidence day is not verified current-day equity.
The worker uses fresh complete sibling fill economics and actual closed midnight marks when
needed ([ADR 0120](../../docs/decisions/0120-verified-risk-opening-evidence.md)). Missing opening
provenance blocks new risk; resetting a latch cannot establish it.

An occupied product runtime (`open` / `pending_exit`) with no position for that product stays
`unknown` / `open_unverified`, even when another product survives and every collection read
succeeds. This holds for running, paused and stopped deployments. Reason
`runtime_position_unresolved` does not supply a quantity; prior coverage/trigger incidents cannot
recover from that absence. Independent sibling/row findings and verified flat products still work.
Portfolio deployment/briefing reads in the portfolio/runtime lanes separately qualify run and
exposure `accounting_complete` and list affected IDs; null totals are unknown, not zero.

## Portfolio vs deployment inventory

Three read-only surfaces answer different questions. Do not conflate them.

| Question | Surface | Access |
| --- | --- | --- |
| Account balances (demo or Coinbase) and portfolio history | Account portfolio | `uv run thytrader-operator portfolio` / `GET /api/v1/operator/portfolio` for balances; `GET /api/v1/portfolio/history?range=7d\|24h\|30d\|forever` for history |
| Deployment quantities, orders, fills, capital, protection | Runtime inventory | `uv run thytrader-runtime show DEPLOYMENT_ID` / `GET /api/v1/deployments/{id}?detail=full` (default `detail=summary` omits historical orders/fills; paginate `.../fills` and `.../orders`) |
| Diagnostic phase/side/protection (coverage quantities only; no prices/cash) | Operator reports | `strategies`, `runtime` (`books[]` redacted) |

`health` may list a `portfolio_history` component (snapshot freshness). That is not holdings.
`risk` and `monitor` omit balances (`balances_omitted: true`). For a portfolio → research recipe
using these surfaces, see the [playbook skill](../thytrader-playbook/SKILL.md#portfolio--research-manual-sequence).

## Exit codes

- `0` overall `healthy` (schema-check success is also `0`)
- `1` overall `degraded`
- `2` overall `failed` (schema-check mismatch is also `2`)
- argparse usage errors use the interpreter's usual non-zero code

Missing telemetry is never treated as healthy. Worker health is PostgreSQL heartbeats, not Docker
`/tmp` readiness files. The market-data worker is stale after two ingest intervals plus slack, not
the 5-second ingest-request poll; it heartbeats between ingest cells and UTC-day chunks
([ADR 0072](../../docs/decisions/0072-catalog-health-bounded-gaps-self-complete-ingest.md)).
Database health is an API engine ping when `THYTRADER_DATABASE_URL` is set.

Health also grades the research worker pool (`research_worker` component,
`payload.research_workers`; [ADR 0092](../../docs/decisions/0092-research-worker-pool.md)): how
many research workers are configured and live, each worker's state, current job, and `rss_bytes`,
and queue depth (`queue.queued`, `queue.running`, `queue.oldest_queued_age_seconds`). A research
job in `queued` is waiting for a free worker, not failed. `RESEARCH_WORKER_MISSING` or
`RESEARCH_WORKER_STALE` mean queued research cannot start; recommend `make run` only when the user
asked to restart. Field details: [report schemas](references/report-schemas.md).

## Workflow

1. Verify CLI help and run `health` first. The CLI compares the API's whole ops contract with
   this checkout's (`thytrader-ops-contract-v88`, schema revision `0073`) and exits on any
   mismatch; read `payload.ops_contract` for the advertised capabilities. Ones this lane relies
   on: `backtest_engine` `thytrader-backtest` (one model, ADR 0083); `strategy_model`
   (`mutable_root`, `auto_snapshot`, `hard_delete`); `spot_quote_currencies` `USD`/`USDC`/`USDT`;
   `decision_journals`; `signal_exit_runtimes`; `reference_instrument_runtimes` with
   `max_reference_instruments` 3; `same_bar_exit_precedence` (`stop`, `take_profit`,
   `signal_exit`, `time_exit`); `catalog_health` (incl. `watch_relative_complete`);
   `research_dataset_autobind` and `study_budgets` (sync 8 candidates / 128 windows, async
   64 / 512); and `runtime_observability` (incl. `position_state`, `fee_adjusted_book_pnl`,
   `capital_normalized_performance`, `explicit_deployment_twins`,
   `rule_matched_deployment_twins`, `exchange_read_failures`, `audit_failure_evidence`).
   `instrument_kinds` lists `spot`, `dated_future` and `perpetual_future`, and
   `futures_order_paths` is empty: Coinbase futures are observation-only, and no command in any
   lane can order one ([ADR 0126](../../docs/decisions/0126-futures-instrument-catalog-read-only.md)).
   Multi-book reads follow [ADR 0060](../../docs/decisions/0060-multi-book-deployment-api.md).
2. If the CLI exits because the API version or ops contract does not match this checkout, rebuild with `make run` (ask first). Package version `0.1.0` is not enough. Do not treat a printed report plus a warning as success.
3. If degraded or failed, follow `recommended_next_action` and inspect `components[].reason_code`.
4. Gather only the extra report needed (market-data, products, strategies, runtime, decisions, performance, reconciliation, studies).
   `products` lists each enabled spot product's order constraints: `price_increment`,
   `base_increment`, `quote_increment`, `base_min_size`, `quote_min_size` (exact decimal
   strings), venue `status` (`online` when trading normally), and `alias` (the product whose
   order book it shares, such as `BTC-USD` for `BTC-USDC`). Check them before sizing an order.
   `products --kind future` (or `all`) adds `payload.futures[]`: each Coinbase futures contract's
   `kind` (`perpetual_future` or `dated_future`), `underlying` (the venue's root unit: `BIP` is
   BTC, never read it from the id), `contract_size`, sizes in **contracts**, margin rates,
   current `funding_rate` and session facts, with `orderable: false`. Futures are observation
   only; nothing can order them. `FUTURES_CATALOG_UNCONFIGURED` means demo mode;
   `FUTURES_CATALOG_UNAVAILABLE` means the listing could not be read or proved complete.
   In `data-catalog`, judge configured coverage by `watch_complete`. For a watched row `complete`
   is the same watch-relative fact ([ADR 0095](../../docs/decisions/0095-sparse-markets-no-trade-bars-listing-floors.md));
   `island_complete` describes only the published dataset. Report coverage as
   `watch_covered_candle_count` of `watch_expected_candle_count` (`watch_coverage_ratio`), and
   `synthetic_no_trade_intervals` as the flat bars published for intervals without trades. A
   `history_floor_at` is the market's listing, proven by a search back past the timeframe's ceiling;
   coverage counts from it. Each row also carries a `watch_status` noun
   (`complete` / `backfilling` / `unknown`) so `worker_status=succeeded` — which describes the
   latest chunk only — cannot be misread as a finished backfill. `sparsity` is island-only; use
   `watch_sparsity` for the configured
   lookback. Failed rows expose redacted `failure_code` / `failure_message` ([ADR 0068](../../docs/decisions/0068-slow-timeframe-watch-lookback-and-catalog-ingest.md));
   `provider_rate_limited` means Coinbase throttled the worker and it is backing off (wait, do not
   re-queue). The catalog checks each newest revision structurally and caches by file identity, so
   it answers in well under a second; exact fingerprints are re-verified when a run binds a dataset
   ([ADR 0085](../../docs/decisions/0085-fast-research-ingest.md)).
   If `watch_complete` is false, use `thytrader-data inspect-gaps` for
   classified holes. If that report sets `truncated`, the `gap_summary` is partial (time/row budget)
   and is not proof the full watch was scanned ([ADR 0072](../../docs/decisions/0072-catalog-health-bounded-gaps-self-complete-ingest.md)).
   Cover HTF-filter and per-indicator extra clocks the same way. Never interpolate.
5. Keep `mode` (`backtest` / `paper` / `live`), timeframe (any ingested venue clock: `1m`, `5m`,
   `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, or `1d`), strategy fingerprint, and dataset fingerprint in
   any answer. Performance `currency` is the strategy snapshot's instrument quote for strategy books or
   the product quote for discretionary books; `null` means provenance could not be established
   and is accompanied by a warning. Never relabel USD amounts as USDC. Performance timeframe is the strategy snapshot's clock, or the discretionary book
   clock. Paper/live `total_net_pnl` is a fill ledger (realized/unrealized, fees, drawdown) marked
   at last close; `MISSING_MARK` means open inventory was not marked. Live REST fill ingest uses
   documented List Fills **cursor** pagination (not `has_next`) and quarantines incomplete or
   unparseable rows ([ADR 0059](../../docs/decisions/0059-coinbase-list-fills-cursor-pagination.md));
   do not treat a truncated or failed fill page as a complete ledger.
   `runtime_observability: capital_normalized_performance` means return/drawdown use the bot's
   pinned budget (`capital.performance_capital_quote` on runtime `show UUID`). Allocations and
   venue balances can change without resetting that denominator. Reported maximum drawdown
   includes fill-event marks and persisted worker observations, surviving recovery/restart;
   it is not a complete historical candle curve. Missing capital or marks leave percentages
   unknown. The runtime breaker measures current drawdown from its durable peak, and latch
   reset preserves history. See [ADR 0107](../../docs/decisions/0107-capital-normalized-live-performance.md).
6. Treat `partial_result_warnings` as incomplete evidence, not as health. A report that fails with
   `Timed out after N s waiting for the ThyTrader API to answer GET …` hit a busy API, not a
   failed one; reads are safe to repeat after a short wait.
7. Separate verified report fields from hypotheses.
8. Stop. Watchlist/ingest/gap-fill require `skills/thytrader-data/SKILL.md` and `--confirm`. Strategy create/save/import/clone/delete and backtests/studies require `skills/thytrader-research/SKILL.md` and `--confirm`. Deploy, pause, resume, stop, fleet controls, live arming, risk-policy publication, and Coinbase credential show/set/clear require `skills/thytrader-runtime/SKILL.md` with `--confirm` unless YOLO covers that tier (live start, live resume, and live place-order also `--i-understand-live`). Credential set/clear always need `--confirm`; YOLO never covers them. Sequencing data → research → optional paper uses `skills/thytrader-playbook/SKILL.md` and still never starts live. Journals, sentiment/pattern hooks, notify, and fail-closed `train` use `skills/thytrader-memory/SKILL.md` with `--confirm`; YOLO never covers that lane.

## Forbidden

- Printing API keys, private keys, `.env` values, or database URLs
- `GET /api/v1/market-data/preview` as the operator contract (dashboard-only)
- Browser clicking as a substitute for these endpoints
- Paper or live order control
- Silently using `--local` because HTTP failed
- Editing application source to "fix" a running instance

See [diagnostics-api.md](references/diagnostics-api.md) and [report-schemas.md](references/report-schemas.md).

YOLO on/off and independent tiers (`data`, `research`, `paper`, `live`) live in `thytrader.yaml`
([ADR 0055](../../docs/decisions/0055-yaml-settings-runtime-reloadable-yolo.md)). They apply without
restart. Leftover `THYTRADER_YOLO_TIERS=paper` is valid; do not JSON-encode the env list.
`GET /api/v1/operator/configuration` reports `yaml_source_of_truth`, `settings_file`,
`yaml_loaded`, and `effective_api_base_url` (the loopback origin agent CLIs resolve for this
checkout — use it instead of probing guessed ports). Mutations use `thytrader-runtime
show-settings` / `set-settings --confirm` or `GET`/`PUT /api/v1/settings`. This skill stays
read-only.

## In-app operator chat

Loopback UI: the **Agent** side panel on every page, or `/chat` as the full page (same component,
[ADR 0079](../../docs/decisions/0079-four-destination-shell-agent-panel-palette-tokens.md)). HTTP:
`/api/v1/operator-chat` ([ADR 0051](../../docs/decisions/0051-in-app-operator-chat.md)). The user pastes **their** LLM API
key into the API process (`PUT /api/v1/operator-chat/credentials`). That is **not** the Coinbase
secrets surface (`/settings` and `thytrader-runtime` show/set/clear-coinbase-credentials). Status
never returns `api_key`. Coinbase keys never go to the browser.

The chat invokes the same versioned HTTP skill routes as these CLIs. Operator tools stay read-only.
Data, research, runtime, and memory mutations wait on in-app confirmation (`--confirm`). Live start,
live resume, and live place-order also need the understand-live checkbox. YOLO never skips understand-live.
Memory always confirms. The playbook never starts live. This skill stays read-only; chat is not
extra trading authority and not a substitute for the lane skills.

`chat-status` reports `llm_configured` only (`thytrader-operator-chat-v1`, not
`thytrader-operator-report-v1`).

For individual held-book fee-adjusted PnL, use the read-only `thytrader-runtime show ID` or
`thytrader-portfolio deployment ID`. Their marked position/book rows carry `entry_fees` and
`unrealized_pnl_net` (gross minus allocated paid entry fees; future exit fees excluded).
Prefer net when present; null means unverified evidence, never zero costs. The operator aggregate
ledger totals retain their existing meanings; do not subtract these per-book fees again.


Paper/live fill comparisons require a saved one-to-one twin link (ADR 0102, ADR 0105;
`explicit_deployment_twins`). Matching fingerprints alone never select partners; with no saved
pair the comparison is absent. Read pairing with `uv run thytrader-runtime show-twin BOT_ID`;
linking and unlinking belong in the [runtime skill](../thytrader-runtime/SKILL.md), always with
`--confirm`. At most 10 saved pairs appear, newest-linked first.

`newest_bar_settle_seconds: 120`: decision `skip_reason: bar_settling` means the newest decision
candle alone is still within its fixed publication wait; no entries are evaluated, and inventory
maintenance continues. `data_gap` after the deadline remains a paused book requiring the usual
runtime lane action; do not automatically resume it. `async_study_planning: worker` and
`strategy_library: origin_counts` are research-lane capabilities.

## Account reads and audit recovery

On `EXCHANGE_UNAVAILABLE`, inspect
`thytrader-operator exchange`: `payload.failure` distinguishes `operation`
(`balances`, `permissions`, `price`, `fees`, `open_orders`, `futures_open_orders`,
`futures_fees`), `kind` (`http`, `timeout`, `network`,
`invalid_response`), and nullable `http_status`. Health component details carry the
same safe summary. Raw provider bodies, URLs and exception messages are omitted.
An unavailable failure object means this error has no classified transport evidence;
never interpret it as a successful read. Do not change credentials merely because a
read failed; inspect the operation and status first.

`thytrader-operator reconciliation` returns one `AUDIT_FAILURES` finding per failure
in the newest 20 audit events. Each `audit_event` identifies `event_id`, `occurred_at`,
`action`, provider/product, and `recovery_status`. `recovered` means a known matching
WebSocket connected event was observed later in this window; `recovery_event_id` and
`recovered_at` link that evidence. It does not establish current feed health: check
`runtime.payload.user_order_feed`. `unresolved` means no matching recovery was seen
in this bounded window; `unknown` means no recovery rule exists for that action.
Order failures stay unknown until actual order reconciliation resolves them.
Recovered failures remain findings and keep the report degraded while in the window.
Audit records are never deleted or rewritten. Non-audit findings have `audit_event: null`.

Account GETs retry once after 0.5 seconds only for timeout/network or HTTP 502/503/504.
The repeated request is freshly signed on the same pagination cursor; exhausted failures
return no partial balances. Authentication, 429 rate limits, malformed responses and
pagination errors do not retry. Failed-read evidence includes `attempts` (1 or 2).
Order submissions and cancellations never use this retry helper.

## Research reliability and protection tracing (ADR 0109)

`products` reports `catalog_fingerprint` and UTC `catalog_observed_at` for its shared
30-second catalog observation. Authoritative product rows precede inferred aliases;
a disabled explicit row stays disabled. A watch mutation verifies an omitted product
through direct provider lookup when supported. Unverifiable refreshes fail closed,
without silently extending stale catalog authority. Read the data skill for mutations.

New bar decisions include optional `protection_update`: canceled and current order
IDs, prior/current stop, target, working coverage and position quantities, and
`fully_covered`. Only confirmed OPEN order remainders count toward coverage; UNKNOWN
or PENDING replacements do not prove coverage. Protective churn retains holding/exit
classification rather than being mistaken for a canceled entry. Legacy rows are
unchanged and may lack this trace. The runtime decision timeline displays the trace.
Read-only campaign/economic tools and bounded exports live in the research skill;
operator observation grants no research mutation or runtime/order authority.

## Safety alert recovery and delivery (ADR 0115)

`uv run thytrader-operator alerts` is read-only. Missing/partial snapshot or candle evidence,
cache warming, a failed inventory read, or a subset inventory never proves an alert
recovered. Verified checks recover independently; a triggered-unfilled stop stays unsafe
until durable order/fill/removal evidence clears it, not merely a price rebound.
Consecutive worker errors persist across restart; unknown/lease-skipped cycles do not
reset them. A supervision entry pause is lease/revision fenced and never auto-resumed;
reconciliation and risk-reducing processing continue. A fresh lease is timing evidence,
not proof that reconciliation/protection succeeded. Implausibly future leases mean unknown
age/possible clock skew, not verified freshness. Counts and health use all open alerts,
even when the displayed feed is bounded; consult partial-result warnings for truncation.

Notification dispatch is separate from safety cycles. `notify_provider=none` leaves the
durable local feed and explicit warning intact. Attempts are durably claimed, bounded,
and retried with stable alert IDs; a webhook receiver must dedupe that ID to prevent duplicate
processing after an ambiguous send/ack crash. Bounded retries can exhaust without receipt;
external delivery is not guaranteed. Delivery errors never reveal the configured destination.

## Backtest fee attribution

`uv run thytrader-operator performance --result-fingerprint sha256:…` includes
`payload.cost_attribution` (`thytrader-cost-attribution-v1`): exact quote-currency
`fill_price_pnl_before_fees`, `entry_fees`, `exit_fees`, and closed-trade `net_pnl`,
plus `trade_count`, `result_fingerprint`, `run_fingerprint`, and its own
`attribution_fingerprint`. Before-fees PnL already includes modeled spread/slippage;
do not subtract them again. `accounting_residual` is net minus (before-fees PnL
minus both fees); `summary_net_pnl_delta` is summary net minus closed-trade net.
Tiny Decimal rounding differences are disclosed separately. Paper/live reports
leave this backtest-only field null; those modes retain their fill-ledger reports.
For bounded research reads/exports and legacy-null warnings, use the research skill.

Health requires the shipped schema revision (`0073`). After updating main, use `make run` to
apply migrations and rebuild the services.

Venue order-state observation time is persisted separately from local `updated_at`
([ADR 0119](../../docs/decisions/0119-venue-order-observation-provenance.md)). A local write
cannot renew venue evidence. Existing rows remain unknown until a successful reconciliation
read; no migration invents a past verification time. This is order-state evidence, not an
independent venue-geometry or whole-account audit, nor a guarantee that a stop-limit will fill.
A repeated revision mismatch after that is a contributor defect, not a reason to
bypass the CLI check or keep restarting unchanged images.

## Futures funding history (ADR 0126)

`uv run thytrader-operator funding` is read-only. Coinbase publishes only the current funding
rate of each perp-style futures contract (for example `BIP-20DEC30-CDE`, nano BTC), so
ThyTrader records it: the market-data worker reads the public futures listing every 5 minutes
when Coinbase credentials are configured. History starts when that poller first ran; there is no
earlier history to fetch.

- `payload.poller`: `last_attempt_at`, `last_success_at`, `consecutive_failures`,
  `failure_code` (`FUTURES_LISTING_UNAVAILABLE` when the listing could not be read or could not
  be proved complete), `contract_count`, `perpetual_count`, and `stale` (no success in the last
  three intervals). `null` means the poller has never run.
- `payload.contracts[]` (one per perp with history, or the one `--product-id`):
  `history_starts_at`, `latest_funding_time`, `latest_rate` (exact decimal per funding interval,
  hourly; longs pay when positive), `latest_settled`, `stored_hours`, `settled_hours`,
  `gap_hours` and up to 24 `gap_times`, `revision_count`, `conflict_count`.
- `payload.rows[]` (only with `--product-id`): every stored hour in the window.

The listing names the most recent funding hour. That hour is **current** while the listing still
names it, and **settled** once the listing names a later hour; the settled rate is the last value
seen while it was current and never changes afterwards. A later disagreeing value is counted in
`conflict_count` (component `FUNDING_RATE_CONFLICT`, plus a `futures_funding_conflict` audit
event) and is not applied. `FUNDING_HISTORY_GAPS` names hours after `history_starts_at` that
were never observed (the worker was down); gaps are never filled or treated as zero.
Contracts with trading sessions (`twenty_four_by_seven: false`, such as index perps) have no
funding hours while closed: their gaps are listed but do not degrade the report.
`FUTURES_POLLER_NOT_RUN` and `FUTURES_POLLER_STALE` point at the market-data worker.
No lane can place a live futures order or adopt or live-deploy a futures contract. Futures
strategies run only as **paper** books, started through `thytrader-runtime` (see Paper futures
books below).

## Futures account mirror (ADR 0127)

`uv run thytrader-operator futures-account` is read-only and shows the newest snapshot of the
Coinbase CFM (US futures) account. The worker records one every 60 seconds when Coinbase
credentials are configured, using four GET reads only (balance summary, positions, intraday
margin setting, current margin window); nothing in ThyTrader can order, close, sweep or change a
futures margin setting.

- `enablement`: `enabled` when the balance summary was read; `unknown` when it was not (see
  `read_failures`, tokens such as `balance_summary:http_401`). `not_enabled` is reserved for a
  documented venue answer; Coinbase documents none, so an account without futures access shows
  `unknown`, never a guess.
- `balance` (all **USD**, `currency: USD`): `futures_buying_power`, `cbi_usd_balance` (spot-side USD
  that CFM can pull as margin), `cfm_usd_balance`, `unrealized_pnl`, `daily_realized_pnl`,
  `funding_pnl`, `initial_margin`, `available_margin`, `liquidation_threshold`,
  `liquidation_buffer_amount` / `_percentage`, and the intraday/overnight margin measures. Never
  add these to USDC or USDT amounts from `portfolio`.
- `positions[]`: `product_id`, `side`, `number_of_contracts` (contracts, not base units),
  prices and PnL in USD. `null` means the position read failed; `[]` means no positions.
- `margin_window_type` (for example `MARGIN_WINDOW_TYPE_OVERNIGHT`), `margin_window_end_at`,
  `intraday_margin_setting`, killswitch flags.
- Reason codes: `OK`, `FUTURES_MIRROR_NOT_RUN`, `FUTURES_MIRROR_STALE` (older than 3 minutes),
  `FUTURES_ACCOUNT_UNKNOWN`, `FUTURES_READ_FAILURES`, `STORE_DISABLED`, `STORE_UNAVAILABLE`.
  External CFM positions are not managed by any bot.
- `margin_ratio` is `available_margin / liquidation_threshold` (`null` on a flat account, which
  has no threshold). `collateral_note`: **Coinbase counts the USDC spot balance as futures
  collateral** (observed 2026-10-10: buying power 514.24 with `cbi_usd_balance` 0.01 and
  `cfm_usd_balance` 0). Futures buying power is the same money as USDC spot capital, not extra
  capacity; never add it to the USDC balance.
- `readiness` adds `payload.futures` (USD amounts, `margin_ratio`, positions, `collateral_note`)
  with findings `FUTURES_COLLATERAL_SHARED` (info), `FUTURES_POSITIONS_EXTERNAL` (advisory) and
  `FUTURES_ACCOUNT_UNKNOWN` (unknown). `venue-reconciliation` adds `payload.futures`: external
  CFM positions (`classification: external_unmanaged`, USD `notional_usd` from contracts x
  contract size x price, `unmanaged_notional_usd`) and nonterminal futures orders from a fresh
  read-only listing, with findings `FUTURES_EXTERNAL_POSITIONS`, `FUTURES_EXTERNAL_ORDERS`
  (info), `FUTURES_POSITIONS_UNKNOWN` and `FUTURES_ORDERS_LISTING_INCOMPLETE` (unknown). They
  are disclosed, never flattened or adopted.

## Paper futures books (ADR 0129)

`uv run thytrader-operator futures-books` is read-only and lists every paper futures book the
runtime lane started (`thytrader-runtime start --mode paper ... --fee-per-contract F` on a
`instrument.kind: future` strategy). Every amount is **USD** (the CFM settlement currency) and is
never added to USDC or USDT amounts; `null` is unknown, never zero.

- `payload.paper_capital_usd` (the policy's `futures.paper_capital_usd`, `null` while unset),
  `committed_paper_cash_usd` (starting cash of running and paused paper futures books),
  `futures_policy_set` (`false`: every futures entry is denied `FUTURES_POLICY_UNSET`; `null`:
  the policy could not be read), `collateral_note`, `live_supported: false`.
- `books[]`: `deployment_id`, `strategy_name`, `status`, `mode`, `product_id`; the binding made at
  start (`contract_kind`, `underlying`, `contract_size`, `fee_per_contract`, `catalog_fingerprint`,
  `bound_at`) and the paper `maker_fee_rate` / `taker_fee_rate`; `side` (`long` \| `short` \|
  `flat`), `contracts` and `base_quantity` (contracts x contract size), `entry_price`,
  `mark_price` / `marked_at` (the last evaluated bar close), `cash`, `equity` (cash + signed
  base quantity x mark), `notional`, `leverage` (notional / equity) and `policy_max_leverage`;
  the latest overnight margin rates (`margin_observed_at`), `initial_margin` and
  `maintenance_margin` (equal: maintenance = initial), `liquidation_buffer_fraction`
  ((equity - maintenance) / equity) against `min_liquidation_buffer_fraction` (0.5 while unset),
  `liquidation_price` (the mark where equity reaches maintenance; `null` when flat or
  unreachable); `funding_total`, `funding_hours`, `recent_funding[]` (newest 24 hours; negative
  `amount` was paid) and `funding_overdue_since`; `daily_loss_latched`.
- `entry_blocks` lists what denies the book's next entry now: `FUTURES_CONTRACT_UNBOUND`,
  `FUTURES_MARGIN_UNKNOWN`, `FUNDING_HISTORY_MISSING` (a held funding hour still unapplied 75
  minutes after it), `FUTURES_POLICY_UNSET`. Gate caps (leverage, buffer, exposure, funding rate)
  are judged per entry and show in `decisions`. `unknown` names unreadable evidence (`binding`,
  `mark`, `margin_rates`, `funding`, `policy`); dependent figures are `null`. Protective and
  liquidation exits are never blocked.
- Component reason codes: `OK`, `NO_FUTURES_BOOKS`, `FUTURES_EVIDENCE_UNKNOWN` (degraded: an
  active book has unknown evidence), `STORE_UNAVAILABLE`.
- To act on a book (stop, flatten, pause) use `skills/thytrader-runtime/SKILL.md`; this report
  never changes anything. A `FUNDING_HISTORY_MISSING` book usually means the market-data
  funding poller is behind: check `uv run thytrader-operator funding --product-id ID`.
