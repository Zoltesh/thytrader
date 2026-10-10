# Architecture Decision Records

Architecture decision records (ADRs) capture choices that materially shape ThyTrader. Never delete or rename one; supersede it with a new ADR and update the status of the old one here and in its header. Number 0067 was never assigned.

## Index

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-sveltekit-frontend.md) | Use SvelteKit/Svelte 5 with TypeScript for the application UI | Accepted |
| [0002](0002-modular-monolith.md) | Start as a modular monolith with separate API and worker processes | Accepted |
| [0003](0003-polyglot-storage.md) | Use PostgreSQL operationally and Parquet/Polars/DuckDB analytically | Accepted |
| [0004](0004-safe-execution-and-access.md) | Use maker-first execution, risk-first exits, loopback-safe deployment, and restrictive credential permissions | Superseded in part by 0006 |
| [0005](0005-canonical-strategy-schema.md) | Use one versioned declarative strategy schema across runtimes | Accepted — extended by 0025, 0026, 0027, 0028, 0029, 0032, 0042, 0047, 0086, 0093, 0096, and 0099 — superseded in part by 0082 |
| [0006](0006-credential-permission-acceptance.md) | Accept operator-selected Coinbase keys with additional permissions | Accepted |
| [0007](0007-immutable-research-run-specifications.md) | Publish immutable research-run specifications before simulation | Accepted — superseded in part by 0082 and 0083 |
| [0008](0008-deterministic-signal-evaluation.md) | Version deterministic signal evaluation separately from request-only runs | Accepted — extended by 0026, 0027, 0028, 0029, 0032, 0042, 0047, and 0086 — superseded in part by 0083 |
| [0009](0009-deterministic-bar-level-backtest-engine.md) | Version bar-level backtest simulation separately from signal evaluation | Superseded by 0083 |
| [0010](0010-constant-spread-backtest-provenance.md) | Version constant-spread stress assumptions as immutable backtest evidence | Superseded by 0083 |
| [0011](0011-derived-buy-and-hold-benchmark.md) | Keep buy-and-hold comparison as a derived backtest report | Accepted |
| [0012](0012-operator-diagnostics.md) | Versioned operator diagnostics CLI/API and confirmation-gated research CLI | Accepted — superseded in part by 0082 |
| [0013](0013-http-first-agent-clients.md) | HTTP-first agent CLIs and confirmation-gated runtime control skill | Accepted |
| [0014](0014-watchlist-and-5m-research.md) | Watchlist ingest and 5m research datasets; paper/live stay 1h | Accepted — superseded in part by 0015, 0016, and 0018 |
| [0015](0015-worker-owned-ingest-and-ops-workspace.md) | Worker-owned ingest jobs, inclusive Coinbase paging, heartbeats, ops workspace | Accepted |
| [0016](0016-longer-complete-5m-datasets.md) | Longer complete 5m datasets via a 25,920-bar cap and chunked UTC-day publish | Accepted |
| [0017](0017-maker-limit-bar-backtest.md) | Maker-limit bar backtest as `thytrader-bar-backtest-v3` | Superseded by 0083 |
| [0018](0018-5m-paper-not-live.md) | Paper may evaluate closed 5m bars; live remains 1h | Accepted — superseded in part by 0036 |
| [0019](0019-ops-contract-identity.md) | Health/CLI ops contract independent of package version `0.1.0` | Accepted — superseded in part by 0083 |
| [0020](0020-complete-only-15m-datasets.md) | Complete-only 15m historical datasets; strategy/paper/live clocks stay 1h/5m | Accepted — superseded in part by 0040 |
| [0021](0021-complete-only-30m-datasets.md) | Complete-only 30m historical datasets; strategy/paper/live clocks stay 1h/5m | Accepted — superseded in part by 0040 |
| [0022](0022-complete-only-6h-datasets.md) | Complete-only 6h historical datasets; strategy/paper/live clocks stay 1h/5m | Accepted — superseded in part by 0040 |
| [0023](0023-complete-only-1d-datasets.md) | Complete-only 1d historical datasets; strategy/paper/live clocks stay 1h/5m | Accepted — superseded in part by 0040 |
| [0024](0024-agent-data-loop-completeness-and-image-identity.md) | Fail-closed watch completeness and stale-image identity for every agent CLI | Accepted |
| [0025](0025-multi-timeframe-htf-filter.md) | Optional HTF filter + LTF entry; closed-bar alignment (paper/live in 0041) | Accepted — paper/live evaluation added by 0041 |
| [0026](0026-phase-9-single-output-indicator-catalog.md) | Phase 9 first slice: `highest`, `lowest`, and population `stdev` | Accepted |
| [0027](0027-phase-9-roc-williams-cci.md) | Phase 9 second slice: `roc`, `williams_r`, and `cci` | Accepted |
| [0028](0028-phase-9-identity-constant.md) | Phase 9 third slice: `identity` OHLCV and `constant` levels | Accepted |
| [0029](0029-phase-9-wma-momentum-mfi.md) | Phase 9 fourth slice: `wma`, `momentum`, and `mfi` | Accepted |
| [0030](0030-agent-e2e-primary-surface.md) | Agent-driven E2E is the primary product surface; UI still required | Accepted |
| [0031](0031-coinbase-first-platform-end-state.md) | Coinbase-first platform end-state: on-demand trades, venue TFs, single- and multi-asset | Accepted — superseded in part by 0046 |
| [0032](0032-phase-9-macd-bollinger.md) | Phase 9 fifth slice: `macd` and `bollinger` with referenceable series ids | Accepted |
| [0033](0033-phase-10-risk-policy-registry.md) | Phase 10 risk-policy registry, capital allocation, concurrent single-instrument paper/live | Accepted — extended by 0056 |
| [0034](0034-phase-12-agent-orchestration-yolo.md) | Phase 12 playbook over existing CLIs and default-off YOLO confirmation opt-in | Accepted — superseded in part by 0043 and 0082 |
| [0035](0035-phase-11-research-rigor.md) | Phase 11 walk-forward, OOS, and cross-market research studies; richer templates; V1/V2/V3 matrix | Accepted — superseded in part by 0082 and 0083 |
| [0036](0036-phase-13-live-extras.md) | Phase 13 5m live, ATR trailing stops, user-order WS, native OCO brackets | Accepted — amended by 0090 |
| [0037](0037-phase-14-experiential-memory.md) | Phase 14 journals, sentiment/pattern hooks, monitor, and config-gated notify | Accepted |
| [0038](0038-complete-only-1m-2h-4h-datasets.md) | Complete-only 1m, 2h, and 4h historical datasets; this slice does not widen clocks | Accepted — superseded in part by 0040 (clocks) |
| [0039](0039-on-demand-discretionary-trades.md) | On-demand long-only discretionary trades with required SL/TP via intent + risk | Accepted |
| [0040](0040-venue-strategy-paper-live-htf-clocks.md) | Every ingested venue TF is a strategy, paper, live, discretionary, and HTF clock | Accepted |
| [0041](0041-paper-live-htf-filter-evaluation.md) | Paper and live evaluate `htf_filter` on last-completed complete-only HTF bars | Accepted |
| [0042](0042-per-indicator-timeframes.md) | Optional per-indicator timeframes on LTF-list indicators; last-completed overlay | Accepted |
| [0043](0043-yolo-live-skip-confirm.md) | Operator-enabled YOLO `live` tier skips `--confirm` on live start/pause/resume/stop; `--i-understand-live` remains | Accepted |
| [0044](0044-parameter-sweeps-wfo-stitched-equity.md) | Parameter sweeps, walk-forward optimization, and derived stitched OOS equity as research composition | Accepted — superseded in part by 0082 |
| [0045](0045-spot-shorting-and-attached-entry-brackets.md) | Spot-capable shorting and attached entry brackets; live shorts fail closed without base | Accepted — amended by 0090 — superseded in part by 0126 (futures data and read surfaces only) |
| [0046](0046-shipped-vs-remaining-0031-destination.md) | Restate 0031: `1m`/`2h` clocks and on-demand are shipped; multi-instrument documents are not | Accepted — superseded in part by 0056 |
| [0047](0047-wider-fail-closed-indicator-catalog.md) | Stochastic, ADX, configurable rolling inputs, and sample stdev | Accepted — extended by 0086 |
| [0048](0048-paper-deploy-fee-fields.md) | Paper deploy maker/taker fee assumptions; live Coinbase fees stay venue-authoritative | Accepted — superseded in part by 0122 |
| [0049](0049-experiential-train-v1.md) | Bounded journal-evidence experiential training V1; advisory research input only | Accepted |
| [0050](0050-daily-loss-drawdown-rate-collars.md) | Daily-loss / drawdown breakers, order-rate limits, and reference-price collars on the Phase 10 registry | Accepted — superseded in part by 0107; amended by 0111 |
| [0051](0051-in-app-operator-chat.md) | Loopback in-app operator chat over gated skill-lane HTTP; user-pasted LLM key distinct from Coinbase | Accepted |
| [0052](0052-richer-sweep-axes-study-catalog.md) | Richer sweep axes and persisted research-study catalog rows | Accepted |
| [0053](0053-workstation-ia-write-only-coinbase-credentials.md) | First-class workstation IA plus write-only Coinbase credentials UI/CLI | Accepted — superseded in part by 0079 |
| [0054](0054-trade-reason-journals.md) | Per-intent why-trade journals; same payload for UI and operator reports | Accepted — surfaces amended by 0080, 0081, and 0087 |
| [0055](0055-yaml-settings-runtime-reloadable-yolo.md) | YAML non-secret settings and runtime-reloadable YOLO | Accepted |
| [0056](0056-multi-instrument-documents-and-pyramiding.md) | Multi-instrument Coinbase USD spot documents and intra-strategy pyramiding | Accepted — superseded in part by 0126 (futures data and read surfaces only) |
| [0057](0057-atomic-fill-ledger-and-product-isolation.md) | Atomic fill ledger and product isolation | Accepted |
| [0058](0058-protection-lifecycle-accounting.md) | Verified protection, leases, stop vs flatten, live capital, durable loss baselines | Accepted — superseded in part by 0106; amended by 0097, 0098, 0107, 0110, and 0112 |
| [0059](0059-coinbase-list-fills-cursor-pagination.md) | Cursor-terminated Coinbase List Fills with fail-closed parsing | Accepted |
| [0060](0060-multi-book-deployment-api.md) | Multi-book deployment HTTP, operator books, and product-tagged orders/fills | Accepted |
| [0061](0061-application-trust-boundary.md) | Application trust boundary: installation Bearer auth, Host/Origin, CSRF session | Accepted |
| [0062](0062-research-paper-semantics-audit-stage-4.md) | Audit stage 4: v4 causal maker engine, runtime clock/indicator parity, truthful OOS fields, dataset numeric identity | Accepted — superseded in part by 0083 |
| [0063](0063-stage-5-release-discipline-ci-risk-defaults-rate-budget.md) | Tracked CI, production web target, live-arming publication gate, optional absolute risk caps, purpose-aware order-rate budget | Accepted |
| [0064](0064-deployment-http-lifecycle-and-breaker-latch-reset.md) | Deployment HTTP ADR 0058 fields, explicit breaker latch reset, operator-chat parity | Accepted |
| [0065](0065-deployment-capital-accounting-http.md) | Deployment `capital` block on HTTP; ledger cash vs allocated/venue quote | Accepted |
| [0066](0066-research-ops-contract-v4.md) | Research ops-contract v25 advertises `thytrader-bar-backtest-v4`; stale v1–v3-only 422 hints rebuild | Accepted — superseded in part by 0083 |
| [0068](0068-slow-timeframe-watch-lookback-and-catalog-ingest.md) | Slow-timeframe 365-day lookback; catalog ingest continuation, gap summaries, failure detail | Accepted — superseded in part by 0085 |
| [0069](0069-async-backtest-jobs-study-summary.md) | Async backtest jobs and bounded study readback; ops contract v27 | Accepted |
| [0071](0071-usdc-spot-quote-markets.md) | USDC spot quote markets across product, strategy, research, risk, and runtime; ops contract v29 | Accepted |
| [0074](0074-multi-book-ledger-bounded-reads.md) | Multi-book fill ledger and bounded deployment list/ledger reads; ops contract v32 | Accepted |
| [0070](0070-mutation-cli-installation-auth.md) | Shared `request_mutation_json()` wires installation Bearer auth on all mutation CLIs | Accepted |
| [0072](0072-catalog-health-bounded-gaps-self-complete-ingest.md) | Bounded gap inspection, self-complete ingest, heartbeat during ingest; ops v30 / Alembic 0043 | Accepted — superseded in part by 0085 |
| [0073](0073-durable-research-jobs.md) | Durable bounded research jobs, plan dedupe, compact planner; ops contract v31 / Alembic 0044 | Accepted — amended by 0092 |
| [0075](0075-fill-atomic-paper-order-status-and-split-state-fail-closed.md) | Fill-atomic paper order status and split-state fail-closed; ops contract v34 | Accepted |
| [0076](0076-selectable-spot-quote-currencies.md) | Selectable USD/USDC/USDT spot quotes; ops contract v35 | Accepted |
| [0077](0077-derived-performance-metrics.md) | Sharpe-class ratios as derived `thytrader-performance-metrics-v1`; ops contract v36 | Accepted |
| [0078](0078-live-readiness-http-ack-venue-reload-definite-rejects.md) | HTTP `i_understand_live` on live start/resume/place-order, execution-worker credential hot reload, definite create rejects vs ambiguous lookup, feed-only pause auto-clear; ops contract v40 | Accepted |
| [0079](0079-four-destination-shell-agent-panel-palette-tokens.md) | Four-destination rail (Home, Strategies, Portfolio, Trade) plus System group, Agent side panel, ⌘K command palette, design tokens with light/dark theme; supersedes 0053 in part | Accepted |
| [0080](0080-per-strategy-workspace-build-test-run-why.md) | Per-strategy workspace (Build · Test · Run · Why) with exact `?version=` context, library pipeline, live preflight from existing endpoints, and redirects from /research, /deploy, /backtests; amends 0054 surfaces | Accepted — superseded in part by 0082 |
| [0081](0081-live-chrome-portfolio-bot-detail-trade.md) | Route-declared global live chrome (amber `LIVE:` strip and frame), Portfolio groups/filter with truthful per-mode capital totals, recomposed bot detail with checkbox-gated live resume, Trade Review aside and live dialog, compact Test run bar with engine default from engine-support | Accepted — superseded in part by 0083 |
| [0082](0082-strategy-root-mutable-strategies-auto-snapshots.md) | Strategy as the root object: one mutable revision-guarded strategy, automatic content-addressed snapshots at backtest/study/deploy start, `strategy_id` foreign keys, hard delete with live-ledger carve-out; ops contract v41 / Alembic 0048 | Accepted |
| [0083](0083-unified-backtest-model.md) | One unified backtest model (`engine: "thytrader-backtest"`, corrected maker-limit semantics) replaces the versioned bar-backtest engines; optional `spread_bps` stress on taker legs; no engine selector; `backtest-model` replaces engine-support; ops contract v42 / Alembic 0049 | Accepted — amended by 0093, 0095, and 0097 |
| [0084](0084-home-kpis-needs-attention-data-health.md) | Home recomposed from existing endpoints: independently loading KPI tiles, Needs attention aggregated from bots, setup, watched datasets, and research jobs, 1D/1W/1M/3M chart with honest gaps on thinned ranges, compact Holdings, fee tier line, and a Data health disclosure | Accepted |
| [0085](0085-fast-research-ingest.md) | Ranged newest-first ingest (350-bar pages, fair per-cycle request budgets, paced with 429 backoff), research lookback ceilings (1m 90 d to 2h-1d 10 y), ingest refuses unwatched targets (409), catalog-grade latest listings and a byte-identical verified-dataset cache; ops contract v45 / Alembic 0052 | Accepted — superseded in part by 0095 |
| [0086](0086-indicator-catalog-expansion-and-offset.md) | 32 more fail-closed indicator kinds (trend, momentum, volatility, volume, statistical), an optional per-declaration `offset` bar lag, one registry rendered into the operator `indicators` report and the builder catalog, four catalog templates, and quote-aware builder copy; ops contract v45 | Accepted — operand-level-lag deferral superseded by 0099 |
| [0087](0087-per-bar-decision-timeline.md) | Durable per-bar decision timeline (`thytrader-bar-decision-v1`, `bar_decisions`) for paper and live bots: outcome, rule values versus thresholds, risk verdict, linked orders; never blocks trading; bounded retention; deployment/strategy HTTP pages, operator `decisions`, `thytrader-runtime decisions`; ops contract v47 / Alembic 0053; amends 0054, 0080, 0081 | Accepted — amended by 0093, 0095, and 0096 |
| [0088](0088-portfolio-model-and-portfolio-backtest.md) | Portfolios foundation: sleeves (one strategy each, capital weights) plus a cash reserve, shared limits, and order-free manager settings, paper or live and never mixed; revision-guarded mutations with an append-only journal; strategy deletion removes its sleeves; async portfolio backtests (`thytrader-portfolio-backtest-v1`) that run each sleeve independently on `weight × capital` over one common window and combine on the union grid (return, drawdown, contribution, correlation, overlap, idle capital, equal-weight basket); `/api/v1/portfolios`, `thytrader-portfolio`, operator `portfolios`; no deployment; ops contract v48 / Alembic 0054 | Accepted — extended by 0091 and 0101; amended by 0092 and 0097 |
| [0089](0089-agent-research-ergonomics.md) | Agent research ergonomics: omitted backtest/study datasets bind the newest complete catalog dataset (echoed as `bound_datasets`, 422 `datasets_missing` otherwise), omitted study bounds use the common covered window, cross-market studies from one strategy plus `markets[].product_id`, sync 8 / async 64 sweep candidates, operator `products` order constraints, retryable 503 for an unverifiable product catalog, and CLIs that name what failed; ops contract v49 | Accepted — amended by 0092 and 0096 |
| [0090](0090-research-correctness-optional-take-profit-diagnostics.md) | Optional take-profit (`kind: none`; live stop-only protection via a Coinbase stop-limit), named entry skip reasons shared by backtest/paper/live (no silent skips; `skipped` decision rows), save-time geometry warnings, backtest entry-funnel diagnostics stored beside results (fingerprints unchanged), account-rate fee suggestions, and an HTTP signal trace behind `thytrader-research-evaluate`; ops contract v48 / Alembic 0055; amends 0008, 0036, 0045 | Accepted — amended by 0093 |
| [0091](0091-portfolio-deployment-limits-and-manager-proposals.md) | Portfolio deployment: one bot per sleeve tagged `portfolio_id` (paper cash / live allocated capital = weight × capital; start planned for every sleeve, refusals start nothing; live needs `i_understand_live`), a live portfolio's sleeve allocations count as risk-policy allocation membership (approach a), portfolio exposure caps in the entry gate and latched daily-loss / drawdown breakers with explicit reason codes, manager proposals (rebalance, pause, resume, add sleeve; rationale + evidence; auto-apply only inside the permissions, live rebalances always wait; never orders) and the one-call briefing; ops contract v51 / Alembic 0056 | Accepted — amended by 0097 |
| [0092](0092-research-worker-pool.md) | Research worker pool: a `research-worker` Compose service (supervisor plus `THYTRADER_RESEARCH_WORKER_COUNT` worker processes, default 2, recycled after N jobs or RSS growth) is the only place backtests, studies, and portfolio backtests run; leased `FOR UPDATE SKIP LOCKED` claims on the existing job tables with heartbeats, crash re-queue, attempt limits, and fenced writes; the API only queues and long-polls (sync submits answer 201, the same 422/503 via `error_code`, or 202 with the job); operator health reports worker liveness, per-worker RSS, and queue depth; ops contract v51 / Alembic 0057; amends 0073, 0088, 0089 | Accepted |
| [0093](0093-signal-based-exits.md) | Signal-based exits: optional `exits.signal_exit` rule tree (entry grammar and operand rules, never HTF-filter ids; omitted from canonical JSON so older fingerprints hold), evaluated on every closed bar after the fill bar and sold as a taker at that close like the time exit (stop and touched take-profit win the bar, signal precedes time), exit reason `signal`, diagnostics `exit_reasons`, trace `exit_condition`, validity `signal_exit_at_close`, a durable position marker so live keeps exiting through pending cancels, decision rows with `exit_rule`, the "Exit when" builder section, and the `ema-trend-hold` template; ops contract v53 / Alembic 0058; amends 0005, 0083, 0087, 0090 | Accepted — amended by 0097 |
| [0094](0094-research-honesty-and-agent-ergonomics.md) | Research honesty and agent ergonomics: every backtest result states its evaluated `window` (outside the result bytes); study summaries carry per-row axis values and bounds, per-candidate OOS sums, and a thinned stitched path; one `validation` shape and document issue paths; decimal request fields accept JSON numbers without moving fingerprints; 12-significant-digit decision operands and honest crossover text; `thytrader-portfolio delete`, `create` with limits/manager, batch `add-sleeves` in one revision, 32 sleeves; `clone-strategy --name`; per-process cache of unsupported USD products; library tag filter and bulk delete by tag; ops contract v53; amends 0044, 0082, 0087, 0088 | Accepted — `create --file` sleeves deferral superseded by 0101; amended by 0098 |
| [0095](0095-sparse-markets-no-trade-bars-listing-floors.md) | Sparse markets keep their history: confirmed no-trade intervals become flat zero-volume bars (counted as `synthetic_no_trade_intervals`, disclosed as `synthetic_no_trade_bars`, filled the same way in paper/live windows and named `no_trade_bar` on decision records), `history_floor_at` only from a backward listing search (daily probes to the lookback ceiling; forward walks never move it; re-proven once per worker process), watch-relative catalog `complete` with coverage X of Y, and Alembic 0059 clearing every pre-existing floor; ops contract v55; supersedes in part 0085; amends 0083, 0087 | Accepted |
| [0096](0096-reference-instruments.md) | Read-only reference instruments: `data_requirements.reference_instruments` (at most 3, same quote currency, decision clock or a coarser integer multiple) read by indicator `source`; closed-bar HTF alignment (no lookahead); derived per-reference warmup; omitted from canonical JSON so older fingerprints hold; reference datasets auto-bind (`bound_datasets` role `reference`) and stay fixed across cross-market studies; paper/live load reference bars every cycle and skip entries fail closed with `reference_data_stale` / `reference_data_missing`; deployment start requires an enabled watch (409 names `thytrader-data watch-add`); `BTC · EMA(100)` operand labels, builder reference block, `btc-regime-gate` template; no cross-instrument orders; ops contract v56; amends 0005, 0087, 0089 | Accepted |
| [0097](0097-runtime-parity-and-observability.md) | Paper follows the backtest's same-bar exit precedence (stop, touched TP, signal exit, time exit; `same_bar_exit_precedence`) with a parity test per combination; `position_state` (`open_protected` vs `exiting`, ...) and `exit_in_flight` beside the raw `phase` on deployment, operator, and sleeve payloads; async `submit-study` waits 30 s (`--submit-timeout-seconds`); operator `portfolios` adds `paper_live_fill_comparisons` for twins matched by strategy fingerprint; ops contract v57; amends 0058, 0083, 0088, 0091, 0093 | Accepted — amended by 0098; pairing amended by 0102 |
| [0098](0098-library-views-book-marks-portfolio-fills.md) | Library Mine / Research / All views (`origin=operator\|research\|all`, research = `claude-research` or `research-*` tags; `list-strategies --origin`); an open paper book's `protection_status` is `covered` on every read (bounded live reads count closing-side orders); last-bar `mark_price` / `unrealized_pnl` on deployment positions and sleeve `books[]` from the decision journal; `GET /api/v1/portfolios/{id}/fill-comparisons` and the Portfolio "Paper vs live" panel; ops contract v58; amends 0058, 0094, 0097 | Accepted — pairing amended by 0102; fee-reporting deferral amended by 0100; live closing-side cover amended by 0112 |
| [0099](0099-operand-level-indicator-offsets.md) | Per-operand native-clock indicator offsets, shared warmup across entry/exits/filter and data binding, lagged trace keys, builder controls, and squeeze template parameter axes; ops contract v59; extends 0005, 0008, 0042, 0044, 0093, 0096; supersedes the operand-lag deferral in 0086 | Accepted |
| [0100](0100-fee-adjusted-open-book-pnl.md) | Marked open-book PnL after allocated paid entry fees, bounded verified fill evidence, explicit net/gross UI basis; future exit fees excluded; ops contract v60 / no migration | Accepted |
| [0101](0101-atomic-portfolio-creation-with-sleeves.md) | Optional initial sleeves in portfolio create API/CLI; full validation and atomic portfolio, sleeves, and journal at revision 1; ops contract v61 / no migration | Accepted |
| [0102](0102-explicit-paper-live-twin-links.md) | Durable one-to-one paper/live comparison links; metadata-only runtime controls; saved pairs replace inference | Accepted — amends 0097 and 0098 |
| [0103](0103-worker-planned-async-studies.md) | Async studies are planned in the research worker, not on the API path | Accepted — amends 0097 |
| [0104](0104-bounded-newest-candle-wait.md) | Bounded wait for a settling newest decision candle before pausing a book | Accepted — amends 0095 |
| [0105](0105-rule-equivalent-clone-twins.md) | Explicit twin links may pair strategy clones whose pinned trading rules verify equal | Accepted — amends 0102 |
| [0106](0106-account-risk-capital-and-live-startup-baselines.md) | Account risk capital, separate bot allocations, and zero-based live startup baselines | Accepted — supersedes in part 0058 |
| [0107](0107-capital-normalized-live-performance.md) | Pinned performance capital and durable observed drawdown for live PnL ledgers; ops contract v64 / Alembic 0061 | Accepted — supersedes in part 0050 and 0058 |
| [0108](0108-account-read-and-audit-failure-evidence.md) | Safe operation-specific account-read errors and individual audit failures with conservative recovery evidence; ops contract v65 | Accepted |
| [0109](0109-research-publication-projections-and-campaigns.md) | Bounded publication evidence and reproducible research campaigns | Accepted — amends 0083, 0090, and 0094 |
| [0110](0110-stopped-lifecycle-reconciliation.md) | Reconcile paused and stopped books fully; keep protection when flatten has no verified price | Accepted — amends 0058 |
| [0111](0111-durable-risk-accounting-scopes.md) | Separate exposure/occupancy from UTC-day loss and latches; strategy-scoped drawdown and retained paper risk evidence | Accepted — UTC opening provenance and flat-book zero fallback superseded by 0120 |
| [0112](0112-quantitative-protection-evidence.md) | Quantitative protection evidence; live cover requires a confirmed matching stop; paper cover stays worker-dependent | Accepted — amends 0058 and 0098 |
| [0113](0113-deploy-anchored-window-cache.md) | Bounded incremental history loading with exact anchored indicator state and explicit cold-cache warming | Accepted |
| [0114](0114-readiness-preflight-and-venue-reconciliation.md) | Advisory capacity and fee preflight; explicit managed-versus-venue inventory evidence | Accepted |
| [0115](0115-durable-safety-alerts-and-supervision.md) | Durable deduplicated safety alerts and execution-worker supervision pauses; Alembic 0066 | Accepted |
| [0116](0116-live-execution-evidence-and-backtest-bar-explanations.md) | Recorded execution quality and bounded reproducible bar explanations; unknown comparisons do not rewrite economics | Accepted |
| [0117](0117-truthful-inventory-and-fleet-controls.md) | Fenced complete inventory and separate consent-bound disarm, managed stop, flatten and rearm | Accepted |
| [0118](0118-watched-market-tail-health.md) | All-enabled-watch published-tail health aligned to each clock; history and worker success stay independent | Accepted |
| [0119](0119-venue-order-observation-provenance.md) | Durable venue order-state observation time independent of local writes; no invented legacy backfill | Accepted |
| [0120](0120-verified-risk-opening-evidence.md) | Authoritative accounting and verified UTC opening reconstruction; legacy stamps remain untrusted | Accepted |
| [0121](0121-execution-write-boundaries.md) | Atomic conditional parent/runtime writes, narrow breaker metadata and serialized same-book fill projection | Accepted |
| [0122](0122-paper-fees-default-to-account-rates.md) | New paper books default to the account's Coinbase fee rates; unreadable rates refuse the start | Accepted |
| [0123](0123-package-layering.md) | Declared package layers with shrink-only upward imports; shared `evaluation` package below every mode | Accepted |
| [0124](0124-inventory-adoption.md) | Live books adopt coins already held at the venue in kind (never routed); live-only; Alembic 0070 | Accepted |
| [0125](0125-correlation-aware-risk-limits.md) | Opt-in BTC-beta-weighted exposure cap and fleet entry clustering cap; fail closed on missing or stale beta; correlation clusters deferred | Accepted |
| [0126](0126-futures-instrument-catalog-read-only.md) | Read-only Coinbase CFM futures (P0): separate instrument model and strict FCM parser (perps detected by funding interval, underlying from `contract_root_unit`, unknown never zero), paged listing that fails closed, spot fingerprints byte-identical, hourly funding history recorded from the listing, 24/7 futures candles in the data lane; no futures order path (`place_order` refuses non-spot ids) | Accepted |
| [0127](0127-cfm-futures-account-mirror.md) | Read-only Coinbase CFM futures account mirror: GET-only four-path allowlisted adapter (balance summary, positions, intraday margin setting, current margin window), exact USD amounts with unknown never zero and non-USD refused, `enabled`/`unknown` enablement, 60-second worker mirror with per-read failure evidence (Alembic 0072), operator `futures-account` report; never summed with spot USDC; observed 2026-10-10 that the USDC spot balance is CFM collateral (shared pool); P0-6 readiness/venue-reconciliation futures sections and Home futures card; §10 same-cycle spot USDC/USD balances (Alembic 0074) and the `futures-account --history` report | Accepted |
| [0128](0128-futures-backtest-model.md) | Futures in the strategy schema (`instrument.kind: future`, `derivatives` block), run spec (contract binding, margin assumption, funding series fingerprint, per-contract fees; excluded when unset) and backtest kernel (contract quantum, overnight margin sizing, hourly funding, conservative liquidation, expiry flatten); spot fingerprints byte-identical | Accepted; P1-3b amended by 0133 |
| [0129](0129-paper-futures-books-and-shared-collateral-risk.md) | Paper futures books and risk on the shared USDC collateral pool: USD/USDC/USD-spot scopes linked logically, never summed; live spot entries denied while manual futures are in use or unknown unless a declared same-currency reserve covers margin under a peg haircut; ADR 0106 capital never counts futures buying power; per-scope exposure caps; beta gross by default, opt-in base-unit netting for managed same-mode books; per-scope daily loss with linked breakers; revised P1 slicing | Accepted |
| [0130](0130-fleet-entry-health.md) | Fleet entry health: per mode and quote scope, the entry gate's own checks run with a neutral probe say whether any new entry is admissible (`yes`/`blocked`/`unknown`) and name the blocking books and records; 24 h decision-log systemic blockers; operator `fleet-health`, `fleet_entries` in readiness/risk/health, Home banner, durable `FLEET_ENTRIES_BLOCKED` alert (ADR 0115) for evidence, latch and policy blocks; ops contract v89 | Accepted |
| [0131](0131-execution-cycle-timing.md) | Execution cycle timing: every execution-worker cycle records phase and per-book timings, Coinbase REST request counts and latency at the SDK HTTP session, SQL statement counts and latency, and deploy-window cache warming in `execution_cycles` (Alembic 0075); `execution_cycle` component with `CYCLE_SLOW` on health and runtime; heartbeat refreshed between books; ops contract v91 | Accepted; §5 amended by 0132 |
| [0132](0132-execution-cycle-shared-reads-and-budget.md) | Execution cycle speed-up with unchanged decisions: the entry gate's per-book fleet reload reads all snapshots with one statement per table; identical product-metadata, preview and fee-tier reads are shared within a cycle (failures never shared, range reads never shared); `CYCLE_SLOW` past interval + 30 s (`CYCLE_WITHIN_BUDGET` otherwise); per-status `book_groups`; ops contract v92 | Accepted |
| [0133](0133-cfm-supervised-trade-evidence-and-fixed-contract-fee.md) | Supervised CFM round-trip evidence: Coinbase bills notional x the futures tier rate plus a fixed $0.11 per contract (Intro: 0.10% taker, 0.095% maker); the `fees` report's `fee_per_contract` is now that fixed part (itemized venue + clearing + regulatory commissions, else the all-in preview total minus taker rate x contract size x preview price; null with a reason otherwise), with `preview_commission_total` kept all-in; margin moves from spot USDC/USD to CFM as a pending transfer (shared pool, ADR 0129); liquidation threshold 0.88 x initial (paper keeps 1.0); no 16:00 ET step-up on the perp; funding not booked hourly; ops contract v93 | Accepted |
| [0134](0134-live-futures-order-path.md) | Live Coinbase CFM futures order path for perp-style contracts (builds on 0126–0129 and 0133): post-only limit entries in whole contracts, every marketable futures order a venue position reduction (`close_position` by default; reduce-only market IOC probed first), product exclusivity, invariants I1–I12 with refusal codes, live book equity from the allocation, kill-switch semantics, slices P2-0–P2-7 unreachable until switch-on with operator visibility per slice, flip guard, flatten without a candle, hourly funding accrual with a drift alert, hedge mode deferred to its own ADR, CLI/HTTP-only live start; synthetic venue fixtures in `tests/exchanges/fixtures/coinbase_futures/` | Proposed |

## Status values

- **Proposed:** under review; not yet a decision, and no code may depend on it.
- **Accepted:** current direction.
- **Superseded:** replaced by a newer ADR; retain for history.
- **Superseded in part / amended:** the named part changed; the rest stands.

## New ADR template

```markdown
# NNNN: Decision title

- Status: Accepted
- Date: YYYY-MM-DD

## Context

What forces and constraints require a decision?

## Decision

What will ThyTrader do?

## Consequences

What becomes easier, harder, required, or intentionally deferred?

## Alternatives considered

What credible alternatives were rejected, and why?
```
