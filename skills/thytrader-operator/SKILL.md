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

Default transport is the loopback HTTP API. The CLI resolves its base URL from `--base-url`, then `THYTRADER_API_BASE_URL`, then the
`THYTRADER_API_HOST` / `THYTRADER_API_PORT` settings (the same `.env` Compose reads; the default
port is `8200`, but installs may override it, so never hard-code a port). For raw `curl`, export
`THYTRADER_API_BASE_URL` and call `"$THYTRADER_API_BASE_URL/api/v1/..."`. Pass `--local` only when you intentionally want process stores instead of HTTP. Do not
fall back from HTTP to PostgreSQL if the API is down. Failures say what failed: a timeout
names the call, an unreachable API names the resolved origin, a dropped connection says to
retry the read, and a report the CLI cannot validate names the field (compare ops contracts
and rebuild with `make run`).

Production installs advertise trust-boundary status at `GET /api/v1/security/status` (no secrets).
Read-only operator routes stay unauthenticated; mutations use installation auth per
[ADR 0061](../../docs/decisions/0061-application-trust-boundary.md).

JSON is the default CLI output. Do not add `--format json` to every command.

## Hard stop

When operating a running instance, do not edit `src/`, `compose.yaml`, Dockerfiles, Alembic, or tests.
Do not search the tree for a code patch. Report failures through this skill. Rebuild or restart only
with `make run` when the user asked to rebuild, or when health/HTTP says the Compose image is stale
(version mismatch, ops-contract mismatch, or 404 on agent routes while `/health/ready` is 200). Open the `ops/` workspace
instead of the git root. Run every `uv run thytrader-*` command from the repository root (the parent
of `ops/`).

## Commands

Prefer the CLI. HTTP is the same contract on loopback.

| Need | CLI | HTTP |
|---|---|---|
| Health | `uv run thytrader-operator health` | `GET /api/v1/operator/health` |
| Configuration | `uv run thytrader-operator configuration` | `GET /api/v1/operator/configuration` |
| Exchange | `uv run thytrader-operator exchange` | `GET /api/v1/operator/exchange` |
| Market data | `uv run thytrader-operator market-data [--product-id BTC-USD] [--timeframe 1h\|5m\|15m\|30m\|6h\|1d\|1m\|2h\|4h]` | `GET /api/v1/operator/market-data` |
| Data catalog | `uv run thytrader-operator data-catalog` | `GET /api/v1/operator/data-catalog` |
| All watched tails | `uv run thytrader-operator data-health` | `GET /api/v1/operator/data-health` |
| Products | `uv run thytrader-operator products` | `GET /api/v1/operator/products` |
| Indicators | `uv run thytrader-operator indicators` | `GET /api/v1/operator/indicators` |
| Strategies / runtimes | `uv run thytrader-operator strategies` | `GET /api/v1/operator/strategies` |
| Runtime watch | `uv run thytrader-operator runtime [--deployment-id UUID]` | `GET /api/v1/operator/runtime` (component `execution_market_data` / `DEMO_MARKET_DATA` when Coinbase credentials are absent and paper books evaluate synthetic demo candles) |
| Monitor | `uv run thytrader-operator monitor` | `GET /api/v1/operator/monitor` (deployments, recent journals, notify delivery; omits balances and webhook URLs) |
| Why-trade review | `uv run thytrader-operator trade-reasons [--intent-id UUID] [--deployment-id UUID]` | `GET /api/v1/operator/trade-reasons` |
| Decision timeline | `uv run thytrader-operator decisions [--deployment-id UUID \| --strategy-id UUID] [--outcome OUTCOME ...] [--limit N] [--cursor C]` | `GET /api/v1/operator/decisions` (per-bar `thytrader-bar-decision-v1` rows, newest first; repeated `outcome`; `next_cursor` paging) |
| Performance | `uv run thytrader-operator performance --result-fingerprint sha256:…` or `--deployment-id UUID` | `GET /api/v1/operator/performance` |
| Risk | `uv run thytrader-operator risk` | `GET /api/v1/operator/risk` (registry identity, slot counts, breaker fractions/ints, pause/mismatch; omits balances) |
| Reconciliation | `uv run thytrader-operator reconciliation` | `GET /api/v1/operator/reconciliation` (every paused `mismatch_detail` is a `STATE_MISMATCH` finding whose `detail` is the mismatch text; split pending-entry state adds `FILLED_WITHOUT_FILL` or `PENDING_ENTRY_WITHOUT_ENTRY`; a live order Coinbase reports FILLED with no List Fills rows (`Filled order has no REST fills.`) adds `FILLED_WITHOUT_FILL` next to `STATE_MISMATCH`; `unknown` orders add `UNKNOWN_ORDERS`; recent audit failures add `AUDIT_FAILURES`) |
| Studies | `uv run thytrader-operator studies` | `GET /api/v1/operator/studies` (persisted research-study catalog rows; omits child equity) |
| Portfolio | `uv run thytrader-operator portfolio` | `GET /api/v1/operator/portfolio` (balances with `balances_omitted=false`; never credentials) |
| Fees | `uv run thytrader-operator fees` | `GET /api/v1/operator/fees` (fee tier plus suggested maker/taker = the account's reported Coinbase rates; `schedule_*` is context only) |
| Portfolios | `uv run thytrader-operator portfolios` | `GET /api/v1/operator/portfolios` (sleeves, issues, allocation, limits, manager settings, `deployable`, `deployment_state`, `breaker_latched` / `breaker_reason_code`, `pending_proposals`, newest portfolio backtest, and `paper_live_fill_comparisons` for explicitly linked paper/live twins with verified identical trading rules; component `PORTFOLIO_BREAKER_LATCHED` when a breaker holds sleeves paused). Edit portfolios and act as the manager with `thytrader-portfolio`; start/stop them with `thytrader-runtime portfolio-*` (ADR 0088, ADR 0091) |
| Readiness preflight | `uv run thytrader-operator readiness [--deployment-id UUID] [--portfolio-id UUID]` | `GET /api/v1/operator/readiness` (advisory allocation vs venue quote vs account and portfolio caps, per-asset caps, remaining entry capacity, paper fee assumptions vs account fee evidence, and which daily-loss breaker binds tighter; never changes policy; [ADR 0114](../../docs/decisions/0114-readiness-preflight-and-venue-reconciliation.md)) |
| Venue reconciliation | `uv run thytrader-operator venue-reconciliation` | `GET /api/v1/operator/venue-reconciliation` (managed live inventory and working orders versus a fresh venue listing; foreign holdings are not errors and are not flattened; incomplete listings stay unknown; [ADR 0114](../../docs/decisions/0114-readiness-preflight-and-venue-reconciliation.md)) |
| Support bundle | `uv run thytrader-operator support-bundle` | `GET /api/v1/operator/support-bundle` |
| Schema check | `uv run thytrader-operator schema-check` | (local files only) |
| In-app LLM key flag | `uv run thytrader-operator chat-status` | `GET /api/v1/operator-chat/status` (HTTP-only; never prints the key; not Coinbase; `--local` is rejected) |

`--format text` is a short summary. Parent flags such as `--format` may follow the subcommand.

Machine-readable envelope: [operator-report-v1.schema.json](references/operator-report-v1.schema.json).

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
`side`, `protection_status`, `position_state`, `exit_in_flight`) without quantities ([ADR 0060](../../docs/decisions/0060-multi-book-deployment-api.md)).
Each row also carries the deployment's worst-book `position_state` / `exit_in_flight`
([ADR 0097](../../docs/decisions/0097-runtime-parity-and-observability.md)). Report a book by
`position_state`, not by `phase`: `phase: pending_exit` includes an open book whose TP/SL
bracket (or stop-only protection) merely rests, which is `open_protected`. Only `exiting`
(`exit_in_flight: true`) means an exit is being sent.
`protection_status` is `flat` / `covered` / `unprotected` / `unknown` from verified attached-child
coverage and venue-visible exits, not inferred parent geometry
([ADR 0058](../../docs/decisions/0058-protection-lifecycle-accounting.md)). An open paper book is
always `covered` (its synthetic stop runs every closed bar), so it agrees with `position_state`
([ADR 0098](../../docs/decisions/0098-library-views-book-marks-portfolio-fills.md)). Rows also include
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

`strategies` and `runtime` also report `ledger_mark_complete`: true when every open product
book has a last-close mark in the decision journal (or the deployment is flat), false when any
open book has no journaled close or the journal cannot be read, and null when its books cannot
be loaded. This uses the same journal marks as `thytrader-runtime show`, without fetching venue
prices or historical fills. The separate `performance` report uses market-data closes and can
still be marked when journal evidence is unavailable.

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

## Portfolio vs deployment inventory

Three read-only surfaces answer different questions. Do not conflate them.

| Question | Surface | Access |
| --- | --- | --- |
| Account balances and portfolio history (demo or Coinbase) | Account portfolio API | `GET /api/v1/portfolio`, `GET /api/v1/portfolio/history?range=7d\|24h\|30d\|forever` — **no** `thytrader-operator` subcommand today |
| Deployment quantities, orders, fills, capital, protection | Runtime inventory | `uv run thytrader-runtime show DEPLOYMENT_ID` / `GET /api/v1/deployments/{id}?detail=full` (default `detail=summary` omits historical orders/fills; paginate `.../fills` and `.../orders`) |
| Diagnostic phase/side/protection without sizes | Operator reports | `strategies`, `runtime` (`books[]` redacted) |

`health` may list a `portfolio_history` component (snapshot freshness). That is not holdings.
`risk` and `monitor` omit balances (`balances_omitted: true`). For a numbered portfolio → research
recipe using these surfaces, see
[`docs/agent/portfolio-research-ops-playbook.md`](../../docs/agent/portfolio-research-ops-playbook.md).

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

1. Verify CLI help and run `health` first. Expect ops contract `thytrader-ops-contract-v67`
   (`research_dataset_autobind` `backtest`/`study` and `study_budgets` sync 8 candidates / 128
   windows, async 64 / 512; [ADR 0089](../../docs/decisions/0089-agent-research-ergonomics.md)),
   Alembic revision `0061`, `indicator_operand_offset_runtimes` `research`/`paper`/`live`
   (native-clock operand lags; [ADR 0099](../../docs/decisions/0099-operand-level-indicator-offsets.md)),
   `portfolio_sleeve_operations` `batch_add`/`create_with_sleeves` (atomic portfolio definition
   creation at revision 1 in the portfolio lane;
   [ADR 0101](../../docs/decisions/0101-atomic-portfolio-creation-with-sleeves.md)),
   `research_worker_pool` (leased research worker pool;
   [ADR 0092](../../docs/decisions/0092-research-worker-pool.md)), `signal_exit_runtimes`
   `research`/`paper`/`live` (`exits.signal_exit`;
   [ADR 0093](../../docs/decisions/0093-signal-based-exits.md)), `reference_instrument_runtimes`
   `research`/`paper`/`live` with `max_reference_instruments` 3 (read-only reference instruments;
   [ADR 0096](../../docs/decisions/0096-reference-instruments.md)), `take_profit_kinds` `reward_risk`/`none`, `live_protection_kinds`
   `trigger_bracket`/`stop_limit`, `backtest_diagnostics`, `fee_suggestion_source`
   `coinbase_account` ([ADR 0090](../../docs/decisions/0090-research-correctness-optional-take-profit-diagnostics.md)),
   `decision_journals` `paper`/`live` (per-bar decision timeline;
   [ADR 0087](../../docs/decisions/0087-per-bar-decision-timeline.md)), `portfolio_model` (sleeves, shared limits, manager settings, journal,
   portfolio backtest; [ADR 0088](../../docs/decisions/0088-portfolio-model-and-portfolio-backtest.md); plus deployment, portfolio limits, and manager proposals with `portfolio_deployment`, `portfolio_breakers`, `portfolio_proposal_kinds`, and `portfolio_briefing_contract` `thytrader-portfolio-briefing-v1`; [ADR 0091](../../docs/decisions/0091-portfolio-deployment-limits-and-manager-proposals.md)), `backtest_engine` `thytrader-backtest` (one unified backtest model;
   [ADR 0083](../../docs/decisions/0083-unified-backtest-model.md)), `strategy_model` (`mutable_root`, `auto_snapshot`, `hard_delete`;
   [ADR 0082](../../docs/decisions/0082-strategy-root-mutable-strategies-auto-snapshots.md)), `spot_quote_currencies` `USD`/`USDC`/`USDT`, `catalog_health`, bounded
   deployment reads (`list`, `summary`, `fills`, `orders`), cursor ledger pagination, and
   multi-book ledger on a current image ([ADR 0074](../../docs/decisions/0074-multi-book-ledger-bounded-reads.md),
   [ADR 0064](../../docs/decisions/0064-deployment-http-lifecycle-and-breaker-latch-reset.md),
   [ADR 0065](../../docs/decisions/0065-deployment-capital-accounting-http.md),
   [ADR 0066](../../docs/decisions/0066-research-ops-contract-v4.md),
   [ADR 0068](../../docs/decisions/0068-slow-timeframe-watch-lookback-and-catalog-ingest.md),
   [ADR 0069](../../docs/decisions/0069-async-backtest-jobs-study-summary.md),
   [ADR 0071](../../docs/decisions/0071-usdc-spot-quote-markets.md),
   [ADR 0072](../../docs/decisions/0072-catalog-health-bounded-gaps-self-complete-ingest.md),
   [ADR 0073](../../docs/decisions/0073-durable-research-jobs.md),
   [ADR 0085](../../docs/decisions/0085-fast-research-ingest.md)). `catalog_health` includes
   `ranged_backfill`, `explicit_watch_ingest`, `research_lookback_ceilings`, and (sparse markets,
   [ADR 0095](../../docs/decisions/0095-sparse-markets-no-trade-bars-listing-floors.md))
   `no_trade_bars`, `listing_history_floor`, and `watch_relative_complete`.
   `same_bar_exit_precedence` (`stop`, `take_profit`, `signal_exit`, `time_exit`: paper and the
   backtest resolve a same-bar tie in that order) and `runtime_observability` (`position_state`,
   `exit_in_flight`, `paper_live_fill_comparison`;
   [ADR 0097](../../docs/decisions/0097-runtime-parity-and-observability.md); plus
   `paper_protection_covered`, `book_marks`, `fee_adjusted_book_pnl`, `portfolio_fill_comparisons`, and `strategy_library`
   `origin_filter`; [ADR 0098](../../docs/decisions/0098-library-views-book-marks-portfolio-fills.md)). Mismatch means
   rebuild with `make run`.
2. If the CLI exits because the API version or ops contract does not match this checkout, rebuild with `make run` (ask first). Package version `0.1.0` is not enough. Do not treat a printed report plus a warning as success.
3. If degraded or failed, follow `recommended_next_action` and inspect `components[].reason_code`.
4. Gather only the extra report needed (market-data, products, strategies, runtime, decisions, performance, reconciliation, studies).
   `products` lists each enabled spot product's order constraints: `price_increment`,
   `base_increment`, `quote_increment`, `base_min_size`, `quote_min_size` (exact decimal
   strings), venue `status` (`online` when trading normally), and `alias` (the product whose
   order book it shares, such as `BTC-USD` for `BTC-USDC`). Check them before sizing an order.
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
8. Stop. Watchlist/ingest/gap-fill require `skills/thytrader-data/SKILL.md` and `--confirm`. Strategy create/save/import/clone/delete and backtests/studies require `skills/thytrader-research/SKILL.md` and `--confirm`. Deploy, pause, resume, stop, live arming, risk-policy publication, and Coinbase credential show/set/clear require `skills/thytrader-runtime/SKILL.md` with `--confirm` unless YOLO covers that tier (live start also `--i-understand-live`). Credential set/clear always need `--confirm`; YOLO never covers them. Sequencing data → research → optional paper uses `skills/thytrader-playbook/SKILL.md` and still never starts live. Journals, sentiment/pattern hooks, notify, and fail-closed `train` use `skills/thytrader-memory/SKILL.md` with `--confirm`; YOLO never covers that lane.

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
Data, research, runtime, and memory mutations wait on in-app confirmation (`--confirm`). Live start
and live place-order also need the understand-live checkbox. YOLO never skips understand-live.
Memory always confirms. The playbook never starts live. This skill stays read-only; chat is not
extra trading authority and not a substitute for the lane skills.

`chat-status` reports `llm_configured` only (`thytrader-operator-chat-v1`, not
`thytrader-operator-report-v1`).

For individual held-book fee-adjusted PnL, use the read-only `thytrader-runtime show ID` or
`thytrader-portfolio deployment ID`. Their marked position/book rows carry `entry_fees` and
`unrealized_pnl_net` (gross minus allocated paid entry fees; future exit fees excluded).
Prefer net when present; null means unverified evidence, never zero costs. The operator aggregate
ledger totals retain their existing meanings; do not subtract these per-book fees again.


Paper/live fill comparisons require a saved one-to-one twin link (ADR 0102; capability
`explicit_deployment_twins`). Matching fingerprints alone no longer select partners. If no pair
is saved, the comparison is absent; unavailable link storage warns instead of guessing.
Use `uv run thytrader-runtime show-twin BOT_ID` to read pairing metadata. Linking/unlinking belongs
in the [runtime skill](../thytrader-runtime/SKILL.md), always with `--confirm`, never in this
read-only operator lane. At most 10 saved pairs appear, newest-linked first.

Ops contract v63 also advertises `async_study_planning: worker`, `newest_bar_settle_seconds: 120`,
and `strategy_library: origin_counts`. Decision `skip_reason: bar_settling` means the newest decision
candle alone is still within its fixed publication wait; no entries are evaluated, and inventory
maintenance continues. `data_gap` after the deadline remains a paused book requiring the usual
runtime lane action; do not automatically resume it.

Ops contract v63 adds `runtime_observability: rule_matched_deployment_twins`
([ADR 0105](../../docs/decisions/0105-rule-equivalent-clone-twins.md)). Intended paper/live clones
can link when their pinned rules match exactly; the server ignores only root id, name, description,
creation time, and metadata. Each fill-comparison side exposes its actual `strategy_fingerprint`;
the top-level fingerprint remains the paper-side reference. This never changes a bot or its rules.

## Account reads and audit recovery

Ops contract v65 advertises `exchange_read_failures` and `audit_failure_evidence` in
`runtime_observability` (Alembic remains `0061`). On `EXCHANGE_UNAVAILABLE`, inspect
`thytrader-operator exchange`: `payload.failure` distinguishes `operation`
(`balances`, `permissions`, `price`, `fees`), `kind` (`http`, `timeout`, `network`,
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

The current fee-attribution migration and health contract both require schema revision
`0068`. After updating main, use `make run` to apply migrations and rebuild the services.

Venue order-state observation time is persisted separately from local `updated_at`
([ADR 0119](../../docs/decisions/0119-venue-order-observation-provenance.md)). A local write
cannot renew venue evidence. Existing rows remain unknown until a successful reconciliation
read; no migration invents a past verification time. This is order-state evidence, not an
independent venue-geometry or whole-account audit, nor a guarantee that a stop-limit will fill.
A repeated revision mismatch after that is a contributor defect, not a reason to
bypass the CLI check or keep restarting unchanged images.
