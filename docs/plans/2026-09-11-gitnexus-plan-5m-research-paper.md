# GitNexus Engineering Plan

> Task: Sequenced path from months of complete 5m history, through a maker-aware backtest and paper/live PnL ledger, to same-clock 5m paper (not live).
> Evidence verified at commit 72b4df55a6561c3042fd8cd714139d5a41f6374f; GitNexus index refreshed this session (`node .gitnexus/run.cjs analyze --index-only --pdg`, cli 1.6.11, runner_identity current). MCP `query`/`context` envelopes still printed `commitsBehind: 1`; the context resource commit matches HEAD — treat those envelopes as stale cache, not index lag.
> Evidence provenance schema 2; global dirty digest 0a9c85780067d9afcd0764f307b60891e3cee927ee11eaeb5ec7826d10fd82cd; cited-path manifest 34 sorted entries; exact generated plan path excluded.

## 1. Objective

Give an agent a research→paper path that is the same clock and the same fill economics: months of complete, fingerprint-addressed 5m bars (no interpolation), a backtest that rests maker limits and expires unfilled the way the worker does, operator performance that reports real PnL, then **paper** (not live) on a published 5m strategy. Live 5m, strategy-vocabulary expansion, parameter sweeps, and microstructure stay out of this execution sequence.

## 2. Current Behaviour

Ingest already pages Coinbase at 350 bars and publishes only gap-free Parquet via `DatasetStore.write` / `extend` (day partitions, content fingerprints). [verified] `MAX_HISTORICAL_INTERVAL_COUNT = 4_032` in `src/thytrader/market_data/models.py:13-15` is the product cap: 14 days of 5m, 168 days of 1h. Watch/config already allow `lookback_hours` up to 2160 (90 days) (`MarketDataWatchTarget.__post_init__`, `WatchTargetRequest`, `Settings.market_data_worker_lookback_hours`), but `bounded_lookback_start` clips `span` to `interval.duration * max_intervals` so a 90-day 5m watch still backfills 14 days. [verified] `_plan_range` uses that lookback only for `INITIAL_BACKFILL`; incremental extend is one-bar overlap forward. [verified] `ingest_once` fail-closes the **entire** request on `_matches_complete_request` (`report.complete` and zero gaps). Coinbase `get_historical_range` independently rejects `interval_count > _MAX_RANGE_INTERVAL_COUNT` (aliased to the same 4032). [verified] ADR 0014 explicitly deferred 5m paper until research coverage is trustworthy and recorded the 4032 raise as enough for seven-day 5m lookbacks.

Paper/live: `create_deployment` raises if `definition.timeframe != "1h"` (`execution/service.py:47-48`). [verified] PDG: that guard is `'T'` → `ExecutionConflictError`; `'F'` continues to the single-running-deployment check. `CandleInterval.execution_supported` returns 1h-only and has **no callers** (definition-only; text search). [verified] The execution worker is a second 1h clock: `_closed_window` calls `get_hourly_range` / `get_hourly_preview` and snaps `ends_at` to minute 0; `new_closed_bars` / `_hourly_contiguous` step `timedelta(hours=1)`. [verified] `test_five_minute_strategy_cannot_start_paper` asserts HTTP 409 with `"1h"` in the detail.

Fill mismatch: `_simulate_backtest` docstring and loop always fill a pending long at the **next candle open** with `fill_model.buy(candle.open, …)` and **taker** fees (`kernel.py:117-151, 309-341`). [verified] `BarExecutionAssumptions.fill_timing` is the literal `"next_candle_open"` only. V1 is `MarkFillModel`; V2 is `ConstantSpreadFillModel` (ask/bid + slippage, `fill_policy: "full"`). The worker places `POST_ONLY_LIMIT` at `broker.maker_limit_price(..., mark=candle.close)`, fills later iff the closed bar trades through (`PaperBroker.match_open_order`: buy fills when `candle.low <= order.price`), cancels/reprices after `max_entry_wait_bars`, and can stop on the **fill bar** (`_manage_position` still checks `candle.low <= position.stop_price` when `entered_bar == candle.starts_at`). [verified] Paper fills record `fee=Decimal("0")`. [verified]

PnL: `OperatorDiagnostics._deployment_performance` always sets `total_net_pnl=None`, hardcodes `timeframe="1h"`, status `DEGRADED` / `RUNTIME_SLICE`, warning that paper/live is a fill-count slice. [verified] Fills already persist (`execution_fills`: price, quantity, fee, filled_at). Backtest `_summary` already computes net PnL, return, drawdown from trades + equity curve.

## 3. Relevant Architecture

Clusters: Market_data / Market_data_worker (ingest + Parquet), Research + Backtest (immutable runs, `thytrader-bar-backtest-v1|v2`), Execution + Execution_worker (closed-bar paper/live), Operator (read-only reports), Data_control (`thytrader-data`), Strategies (declarative schema). ADR 0009/0010: fill/PnL semantics are identity-bearing — a maker engine is a **new** contract, not a silent v1/v2 change. ADR 0014/0015: complete-only worker publication; no interpolation; `create_deployment` 1h-only until 5m research is proven. Storage: PostgreSQL for deployments/fills; Parquet+manifests for datasets. `MarketDataService.get_range` already forwards any `CandleInterval` to the provider; hourly helpers are wrappers.

## 4. GitNexus Findings

Primary symbols (source-verified):

- `bounded_lookback_start` — `impact` upstream maxDepth 2 **HIGH** risk; d=1: `inspect_gaps`, `_plan_range`. Quote: `"risk": "HIGH"` with processes `ingest_once`, `get_gaps`, `run_market_data_worker`.
- `ingest_once` — d=1: `_ingest_due_targets` only (LOW at depth 1). Outgoing: `_plan_range`, `fetch_historical_range`, `_publish_verified_range`.
- `_open_position` — `impact` maxDepth 3 **HIGH** risk (processes `_simulate_backtest`, `evaluate_and_publish_backtest`, backtest `main`); d=1 only `_simulate_backtest` (LOW if depth is clamped to 1). Do not waive HIGH via `riskSharedAxes` (LOW).
- `create_deployment` — d=1: `post_deployment` (LOW, exact). HTTP 409 on `ExecutionConflictError`.
- `_deployment_performance` — d=1: `OperatorDiagnostics.performance` (LOW).

Related: `_plan_range`, `CoinbaseMarketData.get_historical_range` (d=1 tests only when `includeTests`; production reaches it via `fetch_historical_range` → `MarketDataService.get_range`), `DatasetStore.write`/`extend`, `_manage_pending_entry`, `new_closed_bars`, `_closed_window`, `ConstantSpreadFillModel`, `PaperBroker.match_open_order`, `ExecutionPreferences`.

`execution_supported` incoming CALLS empty + text search unused — treat as dead; 5m paper must change `create_deployment` and the worker clock, not only the property.

Query envelope staleness (`commitsBehind: 1`) disagrees with `gitnexus://repo/thytrader/context` after the `--pdg` refresh; source at HEAD is authoritative.

## 5. Statement-Level PDG Findings

**`bounded_lookback_start` (controls + flows `lookback_hours`)**  
`lookback_hours` defines `requested` then `span = min(requested, max_span)` (`service.py:258-261`). Remainder alignment `'T'` shifts start forward; empty-range `'T'` raises. Planning: lifting `MAX_HISTORICAL_INTERVAL_COUNT` changes `max_span` for 5m immediately; 1h 4032 hours remains larger than the 2160h watch cap so 1h behaviour is unchanged. `impact mode=pdg line=261` had no block at that line (multi-line assignment) — intra-PDG UNKNOWN; inter-procedural d=1 still `inspect_gaps` + `_plan_range`.

**`_plan_range`**  
`'T'` on prior complete coverage → incremental start = `covered_ends_at - duration`; `'F'` → `bounded_lookback_start` + `INITIAL_BACKFILL`. Months of history cannot accumulate backward through incremental; they must be won on initial (chunked) backfill.

**`ingest_once`**  
Guards: reconcile-current `'T'` return; attempt lock `'T'` return; provider exception → `provider_unavailable`; `_matches_complete_request` `'T'` → `incomplete_range` return (no publish). Planning: a 90-day atomic fetch will almost always hit this guard on any hole. Chunked complete days must publish per complete chunk **before** requiring the union to be complete.

**`_open_position`**  
Guards: `stop_distance <= 0`, `stop_price <= 0`, `notional < min_quote` all `'T'` return `(None, cash)` (unfilled-as-skip, not wait). Entry price from `fill_model.buy(candle.open)`; fee always `taker_fee_rate`. Maker v3 must not reuse this always-fill path.

**`create_deployment` (file-anchored PDG)**  
Line 47 `'T'` rejects non-1h after live-credentials and paper-cash guards and `_load_published`. `impact mode=pdg line=47`: 5 upstream-dependent statements (live/paper guards + load). 5m paper = split this predicate (paper vs live), do not delete the live 1h arm.

**`_deployment_performance`**  
Store failure `'F'` of try → empty FAILED report; success path always DEGRADED fill-count payload (`total_net_pnl=None`). No branch computes PnL today.

## 6. Proposed Changes

### Slice 2 — months of complete 5m (do first)

- **File** `src/thytrader/market_data/models.py` **symbol** `MAX_HISTORICAL_INTERVAL_COUNT` — raise so `lookback_hours=2160` of 5m is representable (`2160 * 12 = 25920`). Keep Coinbase **page** size 350. Constraint: 1h requested span still min(lookback, 2160h) so 1h datasets do not silently jump to 25920 hours. HIGH-risk `bounded_lookback_start` / Coinbase bound both read this constant.
- **File** `src/thytrader/market_data_worker/service.py` **symbols** `ingest_once`, `_plan_range`, `_publish_verified_range`, `_matches_complete_request` — initial backfill walks complete **UTC day** (or complete 350-bar page) chunks oldest-first inside the lookback window; each complete chunk `write`/`extend`; incomplete chunks are not interpolated and remain `inspect_gaps` holes. Do not require the whole lookback to be complete before any publish. Incremental forward path stays one-bar overlap. **Do not** give ingest trading authority.
- **File** `src/thytrader/exchanges/coinbase_market_data.py` **symbol** `get_historical_range` — allow the new interval cap; paging loop already exists. Add a test that a 5m range longer than 4032 pages without dropping the oldest bar (extend existing page-boundary tests).
- **File** `src/thytrader/data_control/service.py` **symbol** `inspect_gaps` — already uses `bounded_lookback_start`; after the cap lift it must classify holes across the full watch lookback, not 14 days.
- **Docs** supersede ADR 0014’s “4032 is enough for seven-day 5m” consequence; document chunked complete publication. Update `skills/thytrader-data` only for the new coverage meaning (not new CLI verbs).
- **Out of this slice:** raising `lookback_hours` past 2160 (needs alembic CHECK + config Field); 15m/30m/1d `CandleInterval`.

### Slice 3 — maker-aware backtest

- **New engine contract** `thytrader-bar-backtest-v3` (ADR following 0009/0010). v1/v2 fingerprints stay byte-identical. [verified] ADR 0009: “Changes to bar fills … require a new engine-contract version”.
- **File** `src/thytrader/research/models.py` **symbol** `BarExecutionAssumptions` — extend `fill_timing` / add maker fields (`limit_at: completed_close`, `unfilled: cancel|reprice` already on strategy `execution`). `BrokerAssumptions.fill_policy` today is `"full"` only; v3 needs a resting-limit policy.
- **File** `src/thytrader/backtest/kernel.py` **symbols** `_simulate_backtest`, `_open_position` (HIGH at depth 3), `_close_if_required` — on signal, rest a buy limit at signal-bar **close** (paper `maker_limit_price`); fill on a later bar iff `low <= limit` (same as `match_open_order`); else wait up to `max_entry_wait_bars` then cancel or reprice; entry fee uses `maker_fee_rate`. After fill, same-bar stop if `low <= stop` (worker `_manage_position`). Time-exit / TP ordering must be specified to match the worker (stop before TP; TP is resting maker, not same-bar OHLC taker — **open question** if v3 TP is resting post-only vs v2 bid-touch). Default: match worker (`_ensure_take_profit` resting exit), not v2 `_close_if_required` high-touch.
- **File** `src/thytrader/backtest/broker.py` — new `MakerLimitFillModel` (or equivalent) beside V1/V2 models; do not change `ConstantSpreadFillModel` arithmetic.
- **File** `src/thytrader/backtest/submission.py` — accept v3; reject mixing v3 maker assumptions into v1/v2.

### Slice 4 — paper/live PnL ledger

- **New domain service** (application layer, not the FastAPI handler) that folds `execution_fills` + open `execution_positions` + a disclosed mark (reuse backtest `_summary` / bid-close when v2-shaped; for paper use last closed mark consistent with the deployment timeframe).
- **File** `src/thytrader/operator/service.py` **symbol** `_deployment_performance` — populate `total_net_pnl`, return, drawdown, fees; set `timeframe` from the published strategy (payload already `SupportedTimeframe` 1h|5m); drop the permanent `RUNTIME_SLICE` once the ledger exists (keep DEGRADED only for missing marks / paused mismatch).
- **File** `src/thytrader/execution/paper.py` **symbol** `match_open_order` — today’s `fee=0` makes paper PnL incomparable to research; record maker (entry) / taker (emergency exit) fees from strategy cost policy or a documented paper fee schedule. Fills already have a `fee` column.
- No new exchange calls. Do not invent interpolation of missing fills; pause/mismatch stays operator risk, not synthetic PnL.

### Slice 1 last — 5m paper, not live

- **File** `src/thytrader/execution/service.py` **symbol** `create_deployment` — paper may start when `parse_candle_interval(definition.timeframe).execution_supported`; **live** still 1h-only. Wire `execution_supported` to include `FIVE_MINUTES` for paper consumption.
- **File** `src/thytrader/execution_worker/service.py` **symbols** `_closed_window`, `new_closed_bars`, `_hourly_contiguous` — step by `CandleInterval.duration`, load via `get_range` / preview for the strategy interval, not `get_hourly_*`.
- **File** `tests/api/test_deployments.py` — paper 5m 201; live 5m still 409.
- Gate: do not land this slice until a 5m dataset longer than 14 days can be bound in research and a v3 backtest exists on that fingerprint. User order is explicit.

## 7. Implementation Sequence

Each step is a coherent commit; stop after any step with tests green.

1. ADR: longer complete 5m + chunked publication; supersede 0014’s 4032 consequence. Note HIGH risk on `bounded_lookback_start`.
2. Raise `MAX_HISTORICAL_INTERVAL_COUNT` to 25920; add tests that 2160h of 5m is not clipped and 2160h of 1h is still lookback-limited (not 25920 hours).
3. Coinbase/demo range bound + paging tests for 5m > 4032 bars (reuse `_inclusive_page_end` behaviour).
4. Chunked `INITIAL_BACKFILL` in `ingest_once`: complete days published via `write`/`extend`; incomplete days skipped; `_matches_complete_request` applies **per chunk**. Worker state coverage = union of published complete contiguous island (newest suffix; older islands remain fingerprint-addressable).
5. `inspect_gaps` + operator catalog tests over a 30-day 5m lookback with a hole (hole classified, no interpolation, latest verified is contiguous).
6. ADR + `thytrader-bar-backtest-v3` types (`BarExecutionAssumptions`, submission literals). Do not simulate yet.
7. Kernel v3 maker-limit / unfilled / same-bar stop. Keep v1/v2 tests asserting unchanged fingerprints (`tests/backtest/test_kernel.py` next-open taker case remains).
8. v3 tests: fill when next bar trades through close-limit; no fill + cancel after `max_entry_wait_bars`; reprice path; maker fee on entry; stop on fill bar. Note HIGH risk on `_open_position` — `evaluate_and_publish_backtest` and CLI must dispatch v3 without altering v1/v2.
9. Paper fill fees + domain PnL from `execution_fills`/`execution_positions` (pure function, unit-tested against a known fill pair).
10. Wire `_deployment_performance`; operator tests; timeframe from strategy; `total_net_pnl` non-null when fills exist.
11. Flip `execution_supported` + `create_deployment` paper/live split; keep live 5m 409.
12. Interval-generic `_closed_window` / `new_closed_bars`; update `tests/execution_worker/test_closed_bars.py` (today “missed hours”).
13. Integration: published 5m strategy + v3 backtest + paper start on 5m closed bars (in-process stores; no live).
14. Docs: ADR 0014 supersession, backtest-simulation.md v3, operator performance semantics, thytrader-data coverage, thytrader-operator PnL, thytrader-runtime 5m paper. Skills: behaviour only, no new “run this CLI” tutorials.
15. Regenerated goldens/fingerprints **once** at the tip if any recorded v3 examples exist (none today for v3).
16. `node .gitnexus/run.cjs analyze --index-only --pdg` after the series (executor Phase 4).

## 8. Test Strategy

Update:

- `tests/market_data_worker/test_ingestion_service.py` — long 5m backfill, hole in the middle, incremental still one-bar overlap, no interpolation.
- `tests/exchanges/test_coinbase_market_data.py` — range > 4032 5m pages; keep oldest-bar-on-full-page.
- `tests/market_data/test_datasets.py` — extend still requires contiguous complete union.
- `tests/backtest/test_kernel.py` — keep `test_simulation_fills_at_next_open_applies_taker_costs_and_closes_at_target`; add v3 maker cases; keep `test_simulation_five_minute_timeframe_uses_five_minute_bars`.
- `tests/execution/test_loop.py` — `test_paper_loop_places_maker_entry_once_then_fills`, `test_unfilled_entry_cancels_after_max_wait` remain the **oracle** for v3.
- `tests/api/test_deployments.py` — paper 5m allowed; live 5m denied.
- `tests/execution_worker/test_closed_bars.py` — 5m contiguity.
- New `tests/operator/test_deployment_performance.py` (or extend `tests/api/test_operator.py`): two fills → realized PnL; open position → unrealized; `fee=0` vs nonzero.

Edge/failure: incomplete Coinbase day; attempt lock; dataset_persistence_failed; v3 run missing maker fields; performance with empty fills; paused mismatch does not invent equity; 5m paper without complete latest bar pauses (`due is None`).

Commands (exist in `pyproject.toml` / AGENTS.md; no Makefile test target):

```bash
uv run pytest tests/market_data_worker/test_ingestion_service.py tests/exchanges/test_coinbase_market_data.py tests/market_data/test_datasets.py
uv run pytest tests/backtest/test_kernel.py tests/backtest/test_submission.py
uv run pytest tests/execution/test_loop.py tests/execution_worker/test_closed_bars.py tests/api/test_deployments.py
uv run pytest tests/api/test_operator.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

## 9. Risk and Impact Analysis

**HIGH (do not waive):** `bounded_lookback_start` (d=1 `inspect_gaps`, `_plan_range`; processes ingest + get_gaps). `_open_position` at depth 3 (`evaluate_and_publish_backtest`, backtest CLI). Changing fill semantics without a new engine contract would silently rewrite immutable research.

**d=1 accounting:**

| Symbol | d=1 dependents |
| --- | --- |
| `create_deployment` | `post_deployment` |
| `ingest_once` | `_ingest_due_targets` |
| `_open_position` | `_simulate_backtest` |
| `_deployment_performance` | `performance` |
| `bounded_lookback_start` | `inspect_gaps`, `_plan_range` |
| `new_closed_bars` | `_process_one` |
| `get_historical_range` (tests included) | four `tests/exchanges/test_coinbase_market_data.py` cases |

Compatibility: v1/v2 backtests must remain loadable. 1h paper/live must keep working. Raising 4032 does not by itself enable 5m paper.

Performance: 25920 5m bars is ~74 Coinbase pages; chunk-by-day bounds memory vs one giant `analyze_range`. Worker cycle time will grow on first backfill — keep ingest on the market-data worker (ADR 0015).

Concurrency: `record_attempt` / catalog lock already serialize publication; chunked backfill must not publish a partial day.

Observability: keep `market_data_ingestion_succeeded` / `incomplete_range`; add a stable code for `chunk_incomplete` vs whole-range failure.

## 10. Files Expected to Change

| File | Symbols | Reason |
| ---- | ------- | ------ |
| `docs/decisions/0014-…` + new ADR(s) | — | Cap, chunked 5m, v3 engine, 5m paper |
| `src/thytrader/market_data/models.py` | `MAX_HISTORICAL_INTERVAL_COUNT`, `execution_supported` | Cap + paper 5m flag |
| `src/thytrader/market_data_worker/service.py` | `ingest_once`, `_plan_range`, `_publish_verified_range`, `_matches_complete_request` | Chunked complete backfill |
| `src/thytrader/exchanges/coinbase_market_data.py` | `get_historical_range` | New interval bound |
| `src/thytrader/data_control/service.py` | `inspect_gaps` | Same lookback window |
| `docs/architecture/backtest-simulation.md` | — | v3 maker semantics |
| `src/thytrader/research/models.py` | `BarExecutionAssumptions`, `ResearchRunSpecification` | v3 literals |
| `src/thytrader/backtest/submission.py` | engine dispatch | v3 |
| `src/thytrader/backtest/broker.py` | new fill model | Maker limit quotes |
| `src/thytrader/backtest/kernel.py` | `_simulate_backtest`, `_open_position`, `_close_if_required` | Maker path |
| `src/thytrader/execution/paper.py` | `match_open_order` | Paper fees |
| `src/thytrader/operator/service.py` | `_deployment_performance` | Real PnL |
| `src/thytrader/execution/service.py` | `create_deployment` | Paper 5m / live 1h |
| `src/thytrader/execution_worker/service.py` | `_closed_window`, `new_closed_bars` | Interval clock |
| tests listed in §8 | — | Oracles |
| `skills/thytrader-data/SKILL.md`, `skills/thytrader-operator/SKILL.md`, `skills/thytrader-runtime/SKILL.md` | — | Coverage / PnL / 5m paper |

## 11. Reusable Implementation Context

```yaml
implementation_context:
  task_summary: >
    Longer complete 5m datasets, then maker-aware backtest v3, then paper/live
    PnL from fills, then 5m paper (not live) on the same published strategy.
  acceptance_criteria:
    - 5m lookback_hours=2160 is not clipped to 4032 bars; holes are classified not interpolated
    - Complete days stitch into fingerprint-addressed Parquet; incomplete days unpublished
    - v1/v2 backtest fingerprints unchanged; v3 matches paper maker/unfilled/stop-on-fill-bar
    - operator performance --deployment-id reports non-null total_net_pnl when fills exist
    - create_deployment paper accepts 5m; live 5m still 409; worker evaluates 5m closed bars
  evidence_provenance: {
  "schema_version": 2,
  "head_commit": "72b4df55a6561c3042fd8cd714139d5a41f6374f",
  "generated_plan_path": "docs/plans/2026-09-11-gitnexus-plan-5m-research-paper.md",
  "global_dirty_digest": {
    "algorithm": "sha256",
    "canonicalization": "gitnexus-evidence-provenance-v2 NUL-framed UTF-8 records",
    "value": "0a9c85780067d9afcd0764f307b60891e3cee927ee11eaeb5ec7826d10fd82cd"
  },
  "cited_path_manifest": [
    {
      "path": "docs/architecture/backtest-simulation.md",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:a97b191dd39c2cdb827c56917fbfa11c2c46cd5b5d784ccbf57337b4ded0bfb0",
      "index_digest": "sha256:a97b191dd39c2cdb827c56917fbfa11c2c46cd5b5d784ccbf57337b4ded0bfb0",
      "worktree_digest": "sha256:a97b191dd39c2cdb827c56917fbfa11c2c46cd5b5d784ccbf57337b4ded0bfb0",
      "untracked_digest": "absent"
    },
    {
      "path": "docs/decisions/0009-deterministic-bar-level-backtest-engine.md",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:13543a567cb6f201508e387c05f91d5c9cb7fd24324c5db899a860ac94420918",
      "index_digest": "sha256:13543a567cb6f201508e387c05f91d5c9cb7fd24324c5db899a860ac94420918",
      "worktree_digest": "sha256:13543a567cb6f201508e387c05f91d5c9cb7fd24324c5db899a860ac94420918",
      "untracked_digest": "absent"
    },
    {
      "path": "docs/decisions/0010-constant-spread-backtest-provenance.md",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:aa3edb7479b797387f50f6cc1def05bc41e812b4128d12a9fc37e8636d08ec2e",
      "index_digest": "sha256:aa3edb7479b797387f50f6cc1def05bc41e812b4128d12a9fc37e8636d08ec2e",
      "worktree_digest": "sha256:aa3edb7479b797387f50f6cc1def05bc41e812b4128d12a9fc37e8636d08ec2e",
      "untracked_digest": "absent"
    },
    {
      "path": "docs/decisions/0014-watchlist-and-5m-research.md",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:787fb5686d8fb978a095c83fac04d829eea00b9a46e653cad27e73593267d44e",
      "index_digest": "sha256:787fb5686d8fb978a095c83fac04d829eea00b9a46e653cad27e73593267d44e",
      "worktree_digest": "sha256:787fb5686d8fb978a095c83fac04d829eea00b9a46e653cad27e73593267d44e",
      "untracked_digest": "absent"
    },
    {
      "path": "pyproject.toml",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:de07ea61a38b3f9a3496a8851c6358dba62b64dea22d52f25ede085b8be09bab",
      "index_digest": "sha256:de07ea61a38b3f9a3496a8851c6358dba62b64dea22d52f25ede085b8be09bab",
      "worktree_digest": "sha256:de07ea61a38b3f9a3496a8851c6358dba62b64dea22d52f25ede085b8be09bab",
      "untracked_digest": "absent"
    },
    {
      "path": "src/thytrader/api/routes/deployments.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:914511e7867b2184ae2433de7b8509737b81bcf0ba9bf62d3a74f21b5857b884",
      "index_digest": "sha256:914511e7867b2184ae2433de7b8509737b81bcf0ba9bf62d3a74f21b5857b884",
      "worktree_digest": "sha256:914511e7867b2184ae2433de7b8509737b81bcf0ba9bf62d3a74f21b5857b884",
      "untracked_digest": "absent"
    },
    {
      "path": "src/thytrader/backtest/broker.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:2605345fffbd9f61a12979d1aba15eb48af32adfe9d8634c04ec3130d2669f95",
      "index_digest": "sha256:2605345fffbd9f61a12979d1aba15eb48af32adfe9d8634c04ec3130d2669f95",
      "worktree_digest": "sha256:2605345fffbd9f61a12979d1aba15eb48af32adfe9d8634c04ec3130d2669f95",
      "untracked_digest": "absent"
    },
    {
      "path": "src/thytrader/backtest/kernel.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:fc615ded309da91e6c36717d0bf65a93e6c36bd6b30c7a33a8d4443cff2ac97b",
      "index_digest": "sha256:fc615ded309da91e6c36717d0bf65a93e6c36bd6b30c7a33a8d4443cff2ac97b",
      "worktree_digest": "sha256:fc615ded309da91e6c36717d0bf65a93e6c36bd6b30c7a33a8d4443cff2ac97b",
      "untracked_digest": "absent"
    },
    {
      "path": "src/thytrader/backtest/submission.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:061b1dba6f44b969d2339b248ed5bce05432c56a8b2f42e3acfc379cd768c7ca",
      "index_digest": "sha256:061b1dba6f44b969d2339b248ed5bce05432c56a8b2f42e3acfc379cd768c7ca",
      "worktree_digest": "sha256:061b1dba6f44b969d2339b248ed5bce05432c56a8b2f42e3acfc379cd768c7ca",
      "untracked_digest": "absent"
    },
    {
      "path": "src/thytrader/config.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:3175c7af244da48f9a4db2c0daab13a638a6251f26f05e7a5e292669df03ba83",
      "index_digest": "sha256:3175c7af244da48f9a4db2c0daab13a638a6251f26f05e7a5e292669df03ba83",
      "worktree_digest": "sha256:3175c7af244da48f9a4db2c0daab13a638a6251f26f05e7a5e292669df03ba83",
      "untracked_digest": "absent"
    },
    {
      "path": "src/thytrader/data_control/models.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:700fce11551b98c4a1b000688da8afbf6d74b039d2c7450691bf5e5a298b6270",
      "index_digest": "sha256:700fce11551b98c4a1b000688da8afbf6d74b039d2c7450691bf5e5a298b6270",
      "worktree_digest": "sha256:700fce11551b98c4a1b000688da8afbf6d74b039d2c7450691bf5e5a298b6270",
      "untracked_digest": "absent"
    },
    {
      "path": "src/thytrader/data_control/service.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:50f796cd54747903afc74741a2bbb244f5b5c2b0b672029e122c88e414c639b7",
      "index_digest": "sha256:50f796cd54747903afc74741a2bbb244f5b5c2b0b672029e122c88e414c639b7",
      "worktree_digest": "sha256:50f796cd54747903afc74741a2bbb244f5b5c2b0b672029e122c88e414c639b7",
      "untracked_digest": "absent"
    },
    {
      "path": "src/thytrader/exchanges/coinbase_market_data.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:aad24fc6dee2e23f5cee569da5134e938f8f978779e9e5e60860939443a0f377",
      "index_digest": "sha256:aad24fc6dee2e23f5cee569da5134e938f8f978779e9e5e60860939443a0f377",
      "worktree_digest": "sha256:aad24fc6dee2e23f5cee569da5134e938f8f978779e9e5e60860939443a0f377",
      "untracked_digest": "absent"
    },
    {
      "path": "src/thytrader/execution/loop.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:a7b3d7e949976feb7e98d474b3c021e17e16c83d26896025391a0e53cdc8a9ad",
      "index_digest": "sha256:a7b3d7e949976feb7e98d474b3c021e17e16c83d26896025391a0e53cdc8a9ad",
      "worktree_digest": "sha256:a7b3d7e949976feb7e98d474b3c021e17e16c83d26896025391a0e53cdc8a9ad",
      "untracked_digest": "absent"
    },
    {
      "path": "src/thytrader/execution/models.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:dcc2d83ef0f9cbceb6a4126ca623a0e45b43e2315c1f5cc5dad514094f61eb22",
      "index_digest": "sha256:dcc2d83ef0f9cbceb6a4126ca623a0e45b43e2315c1f5cc5dad514094f61eb22",
      "worktree_digest": "sha256:dcc2d83ef0f9cbceb6a4126ca623a0e45b43e2315c1f5cc5dad514094f61eb22",
      "untracked_digest": "absent"
    },
    {
      "path": "src/thytrader/execution/paper.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:35570f280df62ec6a44693501902efb496dd3585197bca702af0d6e3cc72cf0c",
      "index_digest": "sha256:35570f280df62ec6a44693501902efb496dd3585197bca702af0d6e3cc72cf0c",
      "worktree_digest": "sha256:35570f280df62ec6a44693501902efb496dd3585197bca702af0d6e3cc72cf0c",
      "untracked_digest": "absent"
    },
    {
      "path": "src/thytrader/execution/service.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:03bf05a58da340294b60cde4e8946509c599b0144b82b731a30767f3ad9c8e02",
      "index_digest": "sha256:03bf05a58da340294b60cde4e8946509c599b0144b82b731a30767f3ad9c8e02",
      "worktree_digest": "sha256:03bf05a58da340294b60cde4e8946509c599b0144b82b731a30767f3ad9c8e02",
      "untracked_digest": "absent"
    },
    {
      "path": "src/thytrader/execution_worker/service.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:233bfd197bff1e2f0436d95f293399b3fcc4caf30b952fe0e2fcd80442f5e7bb",
      "index_digest": "sha256:233bfd197bff1e2f0436d95f293399b3fcc4caf30b952fe0e2fcd80442f5e7bb",
      "worktree_digest": "sha256:233bfd197bff1e2f0436d95f293399b3fcc4caf30b952fe0e2fcd80442f5e7bb",
      "untracked_digest": "absent"
    },
    {
      "path": "src/thytrader/market_data/datasets.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:949b6bb0afa2e0f53f82a71806ad3d237c84a6f589aab6f00b094b697a70bac2",
      "index_digest": "sha256:949b6bb0afa2e0f53f82a71806ad3d237c84a6f589aab6f00b094b697a70bac2",
      "worktree_digest": "sha256:949b6bb0afa2e0f53f82a71806ad3d237c84a6f589aab6f00b094b697a70bac2",
      "untracked_digest": "absent"
    },
    {
      "path": "src/thytrader/market_data/models.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:220bb85d88f5148107a49053ebb77c47191161df624af7666522816f496364d5",
      "index_digest": "sha256:220bb85d88f5148107a49053ebb77c47191161df624af7666522816f496364d5",
      "worktree_digest": "sha256:220bb85d88f5148107a49053ebb77c47191161df624af7666522816f496364d5",
      "untracked_digest": "absent"
    },
    {
      "path": "src/thytrader/market_data/service.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:9595aabe2a8d186bac4e7176532de19386b7b25645d1c4d388fa827b5d548861",
      "index_digest": "sha256:9595aabe2a8d186bac4e7176532de19386b7b25645d1c4d388fa827b5d548861",
      "worktree_digest": "sha256:9595aabe2a8d186bac4e7176532de19386b7b25645d1c4d388fa827b5d548861",
      "untracked_digest": "absent"
    },
    {
      "path": "src/thytrader/market_data/watchlist.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:55ca1efa7b9eaeaaf48d1ac2a2c2409f84de7f26022f8a3885525f3e92f54cab",
      "index_digest": "sha256:55ca1efa7b9eaeaaf48d1ac2a2c2409f84de7f26022f8a3885525f3e92f54cab",
      "worktree_digest": "sha256:55ca1efa7b9eaeaaf48d1ac2a2c2409f84de7f26022f8a3885525f3e92f54cab",
      "untracked_digest": "absent"
    },
    {
      "path": "src/thytrader/market_data_worker/service.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:96b4417111ca83557c4a3f1ac07285b7209f2b7eea1ece01c6c888d177511960",
      "index_digest": "sha256:96b4417111ca83557c4a3f1ac07285b7209f2b7eea1ece01c6c888d177511960",
      "worktree_digest": "sha256:96b4417111ca83557c4a3f1ac07285b7209f2b7eea1ece01c6c888d177511960",
      "untracked_digest": "absent"
    },
    {
      "path": "src/thytrader/operator/models.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:a3cb03f00f01d8e00536fec4f7d079d57a6f7ffa8a4c02ac979a1e009f89fe33",
      "index_digest": "sha256:a3cb03f00f01d8e00536fec4f7d079d57a6f7ffa8a4c02ac979a1e009f89fe33",
      "worktree_digest": "sha256:a3cb03f00f01d8e00536fec4f7d079d57a6f7ffa8a4c02ac979a1e009f89fe33",
      "untracked_digest": "absent"
    },
    {
      "path": "src/thytrader/operator/service.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:136ee57a36ff3d34d2f383ad0c6d9600564a8b5014feab8d5c57df95f112a228",
      "index_digest": "sha256:136ee57a36ff3d34d2f383ad0c6d9600564a8b5014feab8d5c57df95f112a228",
      "worktree_digest": "sha256:136ee57a36ff3d34d2f383ad0c6d9600564a8b5014feab8d5c57df95f112a228",
      "untracked_digest": "absent"
    },
    {
      "path": "src/thytrader/persistence/schema.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:2b344056a892e5757f828420842fae70707d86d6e34ce5b09956dc83aace4109",
      "index_digest": "sha256:2b344056a892e5757f828420842fae70707d86d6e34ce5b09956dc83aace4109",
      "worktree_digest": "sha256:2b344056a892e5757f828420842fae70707d86d6e34ce5b09956dc83aace4109",
      "untracked_digest": "absent"
    },
    {
      "path": "src/thytrader/research/models.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:46880c5af534361d3f466941797d0e7d2ce44194755a3063d58e08f1fca17a83",
      "index_digest": "sha256:46880c5af534361d3f466941797d0e7d2ce44194755a3063d58e08f1fca17a83",
      "worktree_digest": "sha256:46880c5af534361d3f466941797d0e7d2ce44194755a3063d58e08f1fca17a83",
      "untracked_digest": "absent"
    },
    {
      "path": "src/thytrader/strategies/models.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:c70697c72510dbe587cef9373bc690ce353b202a184d5d158a147163e471f3e5",
      "index_digest": "sha256:c70697c72510dbe587cef9373bc690ce353b202a184d5d158a147163e471f3e5",
      "worktree_digest": "sha256:c70697c72510dbe587cef9373bc690ce353b202a184d5d158a147163e471f3e5",
      "untracked_digest": "absent"
    },
    {
      "path": "tests/api/test_deployments.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:b64b9ed3d634e090ccff4dc07bfcb23735b532fd1a41127f4e4d842a14a59eac",
      "index_digest": "sha256:b64b9ed3d634e090ccff4dc07bfcb23735b532fd1a41127f4e4d842a14a59eac",
      "worktree_digest": "sha256:b64b9ed3d634e090ccff4dc07bfcb23735b532fd1a41127f4e4d842a14a59eac",
      "untracked_digest": "absent"
    },
    {
      "path": "tests/backtest/test_kernel.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:0e6235956c949eebcd34eb006e7eb8f28c18b88fe5f9c456fc55b8750a735944",
      "index_digest": "sha256:0e6235956c949eebcd34eb006e7eb8f28c18b88fe5f9c456fc55b8750a735944",
      "worktree_digest": "sha256:0e6235956c949eebcd34eb006e7eb8f28c18b88fe5f9c456fc55b8750a735944",
      "untracked_digest": "absent"
    },
    {
      "path": "tests/exchanges/test_coinbase_market_data.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:2480b3ba342e74a86eccc5ad2418fc3113d639e715bf4239b4118f99499a4477",
      "index_digest": "sha256:2480b3ba342e74a86eccc5ad2418fc3113d639e715bf4239b4118f99499a4477",
      "worktree_digest": "sha256:2480b3ba342e74a86eccc5ad2418fc3113d639e715bf4239b4118f99499a4477",
      "untracked_digest": "absent"
    },
    {
      "path": "tests/execution/test_loop.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:fc5ad57617265f662cf379f38812a75c75583bd13917a47f83f434dbeba92ee9",
      "index_digest": "sha256:fc5ad57617265f662cf379f38812a75c75583bd13917a47f83f434dbeba92ee9",
      "worktree_digest": "sha256:fc5ad57617265f662cf379f38812a75c75583bd13917a47f83f434dbeba92ee9",
      "untracked_digest": "absent"
    },
    {
      "path": "tests/execution_worker/test_closed_bars.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:e81bb799f18fea1f92102df992521338969fa8d5abeeeab6609b0212d122d076",
      "index_digest": "sha256:e81bb799f18fea1f92102df992521338969fa8d5abeeeab6609b0212d122d076",
      "worktree_digest": "sha256:e81bb799f18fea1f92102df992521338969fa8d5abeeeab6609b0212d122d076",
      "untracked_digest": "absent"
    },
    {
      "path": "tests/market_data_worker/test_ingestion_service.py",
      "object_kind": {
        "head": "regular",
        "index": "regular",
        "worktree": "regular",
        "untracked": "absent"
      },
      "state": "clean",
      "rename_from": null,
      "rename_to": null,
      "head_digest": "sha256:44ca1ef408e36da3f71f133bfd961f43ea4152d8909377fece2a2bb9090637db",
      "index_digest": "sha256:44ca1ef408e36da3f71f133bfd961f43ea4152d8909377fece2a2bb9090637db",
      "worktree_digest": "sha256:44ca1ef408e36da3f71f133bfd961f43ea4152d8909377fece2a2bb9090637db",
      "untracked_digest": "absent"
    }
  ]
}
  primary_symbols:
    - symbol: bounded_lookback_start
      file: src/thytrader/market_data_worker/service.py
      lines: "251-276"
      role: Clips ingest/gap windows to MAX_HISTORICAL_INTERVAL_COUNT (HIGH risk)
    - symbol: ingest_once
      file: src/thytrader/market_data_worker/service.py
      lines: "85-175"
      role: Complete-only fetch/publish; must chunk initial backfill
    - symbol: _open_position
      file: src/thytrader/backtest/kernel.py
      lines: "309-360"
      role: Next-open taker entry; HIGH at depth 3 via publish/CLI
    - symbol: create_deployment
      file: src/thytrader/execution/service.py
      lines: "31-71"
      role: Hard 1h reject; split paper vs live
    - symbol: _deployment_performance
      file: src/thytrader/operator/service.py
      lines: "965-1015"
      role: Fill-count slice; total_net_pnl always null
  related_symbols:
    - symbol: _plan_range
      relationship: CALLS bounded_lookback_start
      relevance: INITIAL_BACKFILL vs INCREMENTAL
    - symbol: inspect_gaps
      relationship: CALLS bounded_lookback_start
      relevance: Gap window follows the same cap
    - symbol: DatasetStore.extend
      relationship: called by _publish_verified_range
      relevance: Contiguous complete stitch
    - symbol: _simulate_backtest
      relationship: CALLS _open_position
      relevance: d=1 of fill change
    - symbol: _manage_pending_entry
      relationship: worker oracle
      relevance: Unfilled wait/cancel/reprice
    - symbol: new_closed_bars
      relationship: CALLS from _process_one
      relevance: Hour-hardcoded clock
    - symbol: execution_supported
      relationship: unused property
      relevance: Must be wired; not sufficient alone
    - symbol: PaperBroker.match_open_order
      relationship: fill oracle
      relevance: Trade-through + fee=0
  execution_path:
    - Watch/ingest request with lookback_hours
    - bounded_lookback_start clips to 4032 intervals
    - ingest_once plans INITIAL_BACKFILL or INCREMENTAL
    - fetch_historical_range pages Coinbase but rejects >4032
    - complete-only publish write/extend
    - Research binds dataset_fingerprint; v1/v2 backtest fills next open taker
    - create_deployment rejects non-1h
    - Worker get_hourly_range / hourly contiguity
    - Maker rest at close; match on later bar; operator PnL null
  pdg_constraints:
    - description: Lookback span is min(requested hours, max_intervals * duration); empty range raises
      affected_statements: ["src/thytrader/market_data_worker/service.py:258-276"]
      implementation_consequence: Raising max_intervals is what unlocks 90-day 5m; 1h still min() with 2160h watch cap
    - description: ingest_once must not publish unless _matches_complete_request
      affected_statements: ["src/thytrader/market_data_worker/service.py:156-165"]
      implementation_consequence: Apply this per chunk, not to the whole lookback
    - description: create_deployment 1h guard is after live/paper cash checks and load
      affected_statements: ["src/thytrader/execution/service.py:41-48"]
      implementation_consequence: Split paper vs live; keep live 1h raise
    - description: _open_position always-fills at next open with taker fee unless size guards return None
      affected_statements: ["src/thytrader/backtest/kernel.py:322-341"]
      implementation_consequence: New v3 path; do not reuse always-fill for maker
    - description: _deployment_performance has no PnL branch on success
      affected_statements: ["src/thytrader/operator/service.py:981-1015"]
      implementation_consequence: Replace payload construction, keep store-failure path
  architectural_patterns:
    - pattern: Identity-bearing engine contracts
      example_location: src/thytrader/research/models.py ResearchRunSpecification.engine_contract_version
      usage_guidance: New fill model = new thytrader-bar-backtest-vN; never reinterpret v1/v2
    - pattern: Complete-only Parquet + fingerprint manifests
      example_location: src/thytrader/market_data/datasets.py DatasetStore.write
      usage_guidance: No interpolation; extend requires overlapping complete union
    - pattern: Worker is the only ingest publisher
      example_location: src/thytrader/market_data_worker/service.py ingest_once
      usage_guidance: API ingest queues the worker (ADR 0015)
    - pattern: Thin routes, domain services
      example_location: src/thytrader/api/routes/deployments.py post_deployment
      usage_guidance: Keep 5m/live policy in create_deployment, not the handler
  files_to_modify:
    - file: src/thytrader/market_data/models.py
      symbols: [MAX_HISTORICAL_INTERVAL_COUNT, CandleInterval.execution_supported]
      intended_change: Raise cap; 5m execution_supported for paper
    - file: src/thytrader/market_data_worker/service.py
      symbols: [ingest_once, bounded_lookback_start, _plan_range]
      intended_change: Chunked complete 5m backfill
    - file: src/thytrader/backtest/kernel.py
      symbols: [_simulate_backtest, _open_position]
      intended_change: v3 maker path
    - file: src/thytrader/execution/service.py
      symbols: [create_deployment]
      intended_change: Paper 5m / live 1h
    - file: src/thytrader/operator/service.py
      symbols: [_deployment_performance]
      intended_change: Fill ledger PnL
  tests:
    - file: tests/market_data_worker/test_ingestion_service.py
      scenarios:
        - 2160h 5m watch is not clipped to 14 days
        - hole day unpublished; neighbors stitch if contiguous
        - incremental still overlap-one-bar
    - file: tests/backtest/test_kernel.py
      scenarios:
        - v1 next-open taker unchanged
        - v3 fills iff next bar low <= close limit
        - v3 cancel after max_entry_wait_bars
        - v3 stop on fill bar
    - file: tests/api/test_deployments.py
      scenarios:
        - paper 5m 201
        - live 5m 409
    - file: tests/execution/test_loop.py
      scenarios:
        - existing maker fill and unfilled cancel remain oracles
    - file: tests/api/test_operator.py
      scenarios:
        - deployment performance total_net_pnl set from fills
  verification_commands:
    - uv run pytest tests/market_data_worker/test_ingestion_service.py tests/exchanges/test_coinbase_market_data.py
    - uv run pytest tests/backtest/test_kernel.py tests/execution/test_loop.py tests/api/test_deployments.py
    - uv run pytest
    - uv run ruff check .
    - uv run ruff format --check .
    - uv run ty check
  risks:
    - HIGH bounded_lookback_start and depth-3 _open_position
    - Whole-range complete-only makes months of 5m ingest fail closed without chunking
    - Paper fee=0 hides true paper vs backtest drift
    - Worker hour-hardcoded clock would still run a 5m strategy on 1h bars if only create_deployment changes
  assumptions:
    - "WHAT: 2160 lookback hours (90 days) is the first 'months' target. HOW: do not migrate watchlist CHECK in slice 2 unless the user answers otherwise."
    - "WHAT: Newest contiguous complete island is latest-verified; older islands stay fingerprint-addressable. HOW: assert list_verified still loads the old digest after a hole starts a new write."
    - "WHAT: v3 TP should follow the worker resting-exit path, not v2 high-touch taker. HOW: re-read _ensure_take_profit before implementing v3 exits."
    - "WHAT: MCP query staleness=1 is cache. HOW: gitnexus-work re-reads context resource vs git rev-parse HEAD."
  open_questions:
    - Raise lookback_hours past 2160 (6–12 months) in this series, or stop at 90 days?
    - Chunk by UTC day vs Coinbase 350-bar page?
    - On a hole, is latest-verified the newest island or the longest island?
    - Should paper require a v3 backtest fingerprint before 5m start, or only the same published strategy?
    - Resting TP in v3 vs keep OHLC TP for research-only pessimism?
  avoid:
    - Do not repeat full repository discovery
    - Do not replace established patterns without evidence
    - Do not interpolate missing candles
    - Do not reinterpret thytrader-bar-backtest-v1 or v2
    - Do not arm 5m live, native OCO/stop, trailing, daily-loss kill, user-order WebSockets
    - Do not add VWAP/session/breakout/multi-product or a Python strategy sandbox
    - Do not fold ingest into operator/research skills
    - Do not change create_deployment without the worker 5m clock (split-brain)
    - Do not waive HIGH impact on bounded_lookback_start or _open_position via riskSharedAxes
```

## 12. Assumptions and Open Questions

Assumptions (executor re-verifies):

- 90-day watch cap (2160h) is enough for the first “months” of 5m; raising CHECK/alembic is a follow-up.
- Demo historical provider can synthesize long 5m ranges in tests (verify `src/thytrader/market_data/demo.py` before relying).
- Newest contiguous complete suffix is what paper needs; research can still bind older fingerprints.
- User-stated deferrals (sweeps, vocabulary, 5m live, microstructure) stay out of Proposed Changes.

Open questions:

1. Raise `lookback_hours` above 2160 in this series?
2. Day chunks vs 350-bar pages for initial backfill?
3. After a hole: latest = newest island or longest island?
4. Must 5m paper start require a v3 backtest on the same strategy fingerprint?
5. v3 take-profit: resting maker like the worker, or keep conservative OHLC touch?

Explicitly deferred: 15m/30m/1d intervals; parameter sweeps / walk-forward CLI; paper-vs-backtest comparison UI; VWAP/session/breakout; trailing enablement; native stop/OCO; user-order WebSockets; daily-loss kill (`risk` registry stays unavailable); 5m **live**.

## 13. Definition of Done

- A 5m watch with `lookback_hours=2160` can publish a complete contiguous dataset longer than 4032 bars when the exchange range is complete; holes are visible to `inspect-gaps` and never filled synthetically.
- `thytrader-bar-backtest-v1` and `v2` tests still pass with prior fingerprints; v3 has tests that match `tests/execution/test_loop.py` maker/unfilled behaviour.
- `thytrader-operator performance --deployment-id` returns non-null `total_net_pnl` (and drawdown/return) from the fill ledger, same Decimal discipline as research summaries; timeframe is the strategy’s, not hardcoded `1h`.
- Paper start of a published 5m strategy succeeds; live start of that strategy still 409; the execution worker evaluates 5m closed bars (`get_range`, interval-step contiguity).
- Docs/ADRs/skills updated; `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run ty check` pass.
