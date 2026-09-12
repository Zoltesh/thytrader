# Ops field report: 5m BTC-USD data → research → paper

> **Audience:** the agent implementing [2026-09-11-gitnexus-plan-5m-research-paper.md](./2026-09-11-gitnexus-plan-5m-research-paper.md) and ADRs 0016–0018.
> **Authoring workspace:** `ops/` (running instance). No `src/`, Compose, Alembic, or test edits were made.
> **When:** 2026-09-11, approximately 22:22–22:29 UTC (16:22–16:29 MDT).
> **Verdict:** HEAD already contains the work. The **running Compose image does not**. An ops agent following the skills of record cannot tell those two facts apart from `thytrader-operator health`, and therefore cannot complete the user journey this plan exists to unlock.

This is not a design proposal. It is what happened when a user asked an ops agent to invent a 5m BTC-USD strategy, ingest months of complete 5m history, backtest it with resting maker limits, and paper-trade that exact published fingerprint — with no live, no interpolation, and no source hacks.

---

## 1. Why this report exists

The user goal (paraphrased, constraints kept):

1. Invent a **5-minute BTC-USD** strategy inside what ThyTrader can actually express.
2. Research it on **complete 5m history measured in months**, not a two-week clip.
3. Backtest the way paper fills: **resting maker limits** that can wait, miss, cancel, or reprice — not next-bar-always-fills.
4. **Paper** (not live) that exact published fingerprint, modest simulated cash.
5. Stay with ingest until coverage is **published or holes are classified**. A 202 / queued job is not history.
6. If a feature cannot be expressed, say so and pick a legal alternative.
7. If the stack is stale or a skill/CLI fails, **report and stop that path** — do not patch Python, scrape logs, query PostgreSQL, interpolate candles, or arm live.

That is exactly the plan’s acceptance path. The ops agent did the work through `uv run thytrader-*` from the repository root against `http://127.0.0.1:8200`. Three of the four unique slices failed on the **running API**, while git HEAD and the skill/ADR text already describe success.

If you only read one section after the verdict, read **§8 Suggested fixes**. The rest is evidence.

---

## 2. Environment (facts, not hypotheses)

| Item | Observed value |
|---|---|
| Operator schema | `thytrader-operator-report-v1` |
| `application_version` (health + `/health/ready`) | `0.1.0` |
| Health `overall_status` | `healthy` (exit 0) |
| `/health/ready` | HTTP 200 `{"service":"api","status":"ready","version":"0.1.0"}` |
| Environment (configuration report) | `production`, `containerized: true`, `allow_remote_access: false` |
| API bind | `0.0.0.0:8200` (loopback client default still used) |
| Coinbase | `connected`, `demo: false`, `live_credentials_configured: true` |
| Permissions (redacted report) | `view`, `trade`, **`transfer`** (extra; not treated as consent) |
| Compose project | `thytrader`, config `/home/zoltesh/projects/thytrader/compose.yaml` |
| API / workers image id | `sha256:7933e942e87e6d8fa72497d187fd8653624084a50f3f949df5dc448c157fd3d7` |
| Container created | **2026-09-11 13:10:43 -0600** (~50 minutes up at first probe) |
| Git HEAD at ops time | `c5c2c17` — `Merge pull request #1 from Zoltesh/5m-research-paper` (**15:15:57 -0600**) |
| Local CLI | `uv run thytrader-*` from repo root (source tree at HEAD) talking to **Docker HTTP**, not `--local` |

Workers up and healthy: `api`, `postgres`, `market-data-worker`, `execution-worker`, `worker` (portfolio), `web`.

Configuration payload that mattered later:

- `market_data_worker_interval_seconds`: **300**
- `market_data_worker_lookback_hours`: **168** (process default; not the watchlist 2160)
- `market_data_worker_product_id`: `BTC-USD`
- `execution_worker_interval_seconds`: **30**

Runtime/risk reports were **degraded** independently of instance health:

- `RISK_REGISTRY_UNAVAILABLE` — “Typed risk policies are not yet a supported operator contract.”
- `partial_result_warnings`: “The composable risk-policy registry is not implemented.”
- `recommended_next_action`: treat pause and mismatch findings as the current risk surface.
- No deployments, no pause findings, no unknown-order findings.

**Stale-image detection did not fire.** Skills and `ops/README.md` only authorize `make run` when:

- the user asked to rebuild, or
- CLI stderr reports an API/CLI version mismatch, or
- an agent route is HTTP 404 while `/health/ready` is 200.

None of those happened. Both CLI and API advertise `0.1.0`. Agent routes (`/api/v1/data`, `/api/v1/strategies`, `/api/v1/backtests`, `/api/v1/deployments`) returned 200/201/409/422 — not 404. The ops agent therefore **did not rebuild**, per hard stop.

`ops/README.md` also says “Apply migration `0016` as part of that rebuild.” That is invisible to health. An ops agent cannot know whether 0016 is applied without violating “no database diving.”

---

## 3. Git vs running image (the actual blocker)

HEAD already has the sequenced commits. The running image was built **before** them.

| Time (MDT, 2026-09-11) | What |
|---|---|
| 13:10 | Compose `api` / `market-data-worker` / `execution-worker` / `worker` created from `thytrader-api` image `7933e942…` |
| 13:13 | `72b4df5` feat: queue ingest on the worker and split ops from source |
| 14:11 | `f235e2b` docs(adr): raise 5m research history beyond a 14-day clip |
| 14:14 | `dde50ea` feat(market-data): let 5m lookbacks span the 90-day watch window |
| 14:15 | `93cb114` test(market-data): page 5m ranges longer than the old 4032-bar clip |
| 14:25 | `718ecaf` feat(market-data): publish complete UTC-day ingest chunks |
| 14:29 | `46458b0` feat(data): classify 5m holes across the full watch lookback |
| 14:34 | `448c48b` docs(adr): add thytrader-bar-backtest-v3 types |
| 14:45 | `c0c7dde` feat(backtest): simulate resting maker-limit fills as v3 |
| 14:55 | `526f926` feat(execution): record paper maker/taker fees and fold a fill ledger |
| 14:59 | `8a77e28` feat(operator): report paper and live PnL from the fill ledger |
| 15:05 | `d8330dc` feat(runtime): evaluate published 5m strategies on paper, not live |
| 15:15 | **`c5c2c17` merge to the branch the ops agent was sitting on** |

Net: **~2 hours of the exact work this user asked to operate was not in the process that answered HTTP.** Local `uv run` imported HEAD. Docker served 13:10. That split is how you get a CLI that will *send* `thytrader-bar-backtest-v3` and an API that 422s it.

Do not debug this as “the plan was never implemented.” Debug it as **“version identity is not content identity.”**

---

## 4. What the ops agent actually did (sequence)

All mutations used `--confirm`. Live was never started. `--i-understand-live` was never passed. `--local` was never used as an HTTP fallback.

### 4.1 Operator snapshot

- `thytrader-operator health` → healthy.
- `configuration`, `exchange`, `data-catalog`, `products`, `indicators`, `strategies`, `runtime`.
- `market-data --product-id BTC-USD --timeframe 5m` → **degraded** `MARKET_DATA_NEVER_RUN` (no BTC-USD 5m yet).
- Watchlist before mutation: `BTC-USD` **1h** lookback 168; `ETH-USD` **5m** lookback 168. No BTC-USD 5m.
- Catalog: BTC-USD 1h complete ~2026-07-22 17:00Z–2026-09-11 22:00Z, 1229 bars, fingerprint `sha256:b69f48d3…`. ETH-USD 5m complete ~8 days.
- Existing publications: ETH 5m EMA trend (`sha256:f34c5443…`, prior v1 backtest); two BTC 1h EMA trend fingerprints. **No deployments.**

### 4.2 Data path

```text
thytrader-data watch-add --product-id BTC-USD --timeframe 5m --lookback-hours 2160 --confirm
→ watch stored: lookback_hours=2160, enabled=true

thytrader-data ingest --product-id BTC-USD --timeframe 5m --confirm
→ returned in ~2.2s (CLI poll until ingest flag clear, max 120s)
→ status=succeeded, complete=true, gap_count=0
→ expected=received=4032
→ covered 2026-08-28T22:20:00+00:00 → 2026-09-11T22:20:00+00:00
→ fingerprint sha256:4e7c13c1d431e86387bd97928c86050d2b2e36e901bbda1d5c2cbe377443fb5f

thytrader-data inspect-gaps --product-id BTC-USD --timeframe 5m
→ starts_at/ends_at = the same 14-day island
→ gap_count=0, gaps=[], omitted_gap_count=0, interpolated=false
```

4032 five-minute bars = **14 days exactly**. That is the old `MAX_HISTORICAL_INTERVAL_COUNT` clip (plan §2, ADR 0016 context), not a 90-day backfill.

A later catalog/market-data probe showed the island **extended forward by one complete bar** (worker incremental path):

| When | Bars | End | Fingerprint |
|---|---|---|---|
| First ingest success | 4032 | 2026-09-11T22:20:00Z | `sha256:4e7c13c1d431e86387bd97928c86050d2b2e36e901bbda1d5c2cbe377443fb5f` |
| ~22:29 UTC | 4033 | 2026-09-11T22:25:00Z | `sha256:d9d0adea3178da7e614cbb58ec300a289830b710cb5d34d832f1030da4c2cab1` |

Start stayed `2026-08-28T22:20:00Z`. History did **not** grow backward. Fingerprints move when a new complete bar publishes; bind at research-submit time, do not assume catalog identity is stable across a few minutes.

`fill-gaps` was not run: `inspect-gaps` reported a complete island with zero classified holes. Re-queueing the same clipped ingest would not have created months of history.

### 4.3 Research path

```text
thytrader-research create-draft --product-id BTC-USD --timeframe 5m --confirm
→ strategy_id=01a09294-0d4a-73eb-9d49-c872e56b8a70, revision=1, name="BTC 5m EMA trend"
```

First `save-draft` of an invented bounce (RSI `crosses_above` literal `"40"`):

- CLI stdout: `Research command failed safely; paper and live state were not changed.` **No field errors.**
- Direct `PUT /api/v1/strategies/{id}/versions/1` → **HTTP 422**. Decisive message:

  `Value error, crossover right operand must reference an indicator`

  Input: `{"left":{"indicator":"rsi_14"},"operator":"crosses_above","right":{"literal":"40"}}`

  Plus a large union-type fan-out (`AllCondition` / `AnyCondition` / `NotCondition`) that buries the real error.

Legal rewrite saved and published:

```text
save-draft --revision 1 --confirm
→ strategy_id=01a09294-…, revision=2, version=1

publish --strategy-id 01a09294-0d4a-73eb-9d49-c872e56b8a70 --confirm
→ strategy_fingerprint=sha256:32a6b0a62c329a37e9d36541a34a588bde80172cbc442f479444a04b6862db80
→ version=1
```

Published name: **BTC 5m SMA-trend EMA reclaim**. Timeframe `5m`. Status `published`.

### 4.4 Maker backtest (v3) — stopped

Request (evaluation window omitted so the server would fill the dataset’s usable range):

```json
{
  "strategy_fingerprint": "sha256:32a6b0a62c329a37e9d36541a34a588bde80172cbc442f479444a04b6862db80",
  "dataset_fingerprint": "sha256:4e7c13c1d431e86387bd97928c86050d2b2e36e901bbda1d5c2cbe377443fb5f",
  "initial_quote_balance": "10000",
  "maker_fee_rate": "0.004",
  "taker_fee_rate": "0.006",
  "fixed_slippage_bps": "0",
  "engine_contract_version": "thytrader-bar-backtest-v3"
}
```

```text
thytrader-research submit-backtest --file … --confirm
→ HTTP 422
→ Input should be 'thytrader-bar-backtest-v1' or 'thytrader-bar-backtest-v2'
→ input: thytrader-bar-backtest-v3
```

Running OpenAPI `BacktestSubmissionRequest.engine_contract_version` enum: **v1, v2 only**. String counts in `/openapi.json`: `thytrader-bar-backtest-v3` = 0, `resting_maker_limit` = 0, `post_only_limit` = 0.

`BrokerAssumptions` on the running API is still the v2 block only (`price_model: constant_spread_bps`, `fill_policy: full`, `trigger_evaluation: bid_side`, `equity_marking: bid_close`).

The ops agent **did not** submit v1/v2 as a substitute. The user forbade next-open taker fantasy. Existing ETH 5m research on this instance is v1 (`result_fingerprint sha256:f3593f0a…`, 20 trades, net PnL about −10.48 on $10k) — useful as a contrast, not as this strategy’s evidence.

### 4.5 Paper — stopped

```text
thytrader-runtime start \
  --strategy-fingerprint sha256:32a6b0a62c329a37e9d36541a34a588bde80172cbc442f479444a04b6862db80 \
  --mode paper --cash 10000 --confirm
→ HTTP 409
→ Paper and live deployments require the 1h timeframe.
```

No deployment id. No worker 5m clock. No fill ledger. No operator performance slice. The ops agent did **not** start a 1h paper runtime on a different fingerprint to “get something running.”

Live was not attempted. 5m live must remain 409 after a correct rebuild (ADR 0018).

---

## 5. Slice-by-slice: plan acceptance vs this run

Mapped to the plan’s slices. **Code on HEAD is out of scope for this report**; this is what HTTP did.

### Slice 2 — months of complete 5m (ADR 0016)

| Acceptance (plan / ADR / data skill) | This run |
|---|---|
| `lookback_hours=2160` is representable (25,920 five-minute bars) | Watch **accepted** 2160. Ingest **published 4032** and `complete: true`. |
| `inspect-gaps` classifies holes across the **full watch lookback**, never interpolates | Classified **only the 14-day island**. `interpolated: false` (good). Did **not** emit `not_fetched` (or any cause) for the other ~76 days of the 90-day watch. |
| Incomplete days unpublished; latest verified = newest contiguous complete island | Island was complete and fresh. Oldest bound frozen at first clip; incremental ingest only moved `covered_ends_at` forward one bar. |
| CLI poll until published, not 202-as-done | Ingest CLI returned succeeded in ~2s. That *was* published Parquet for the clip — not a stuck queue. Speed is a smell that the worker never walked 90 UTC days. |

**Operator-visible contradiction:** catalog row `lookback_hours: 2160` plus `expected_candle_count: 4032` plus `complete: true` plus `sparsity: "none"`. A 90-day watch cannot be complete with 14 days of bars unless completeness is defined as “complete relative to the clipped plan,” which is exactly the product lie ADR 0016 exists to remove.

**Worker config vs watchlist:** configuration report still says `market_data_worker_lookback_hours: 168`. Watchlist said 2160. Ingest used the watch target (otherwise ETH 5m vs BTC 5m could not differ), but the process default is another mixed signal for agents.

### Slice 3 — maker-aware backtest (ADR 0017)

| Acceptance | This run |
|---|---|
| `thytrader-bar-backtest-v3` is a named engine contract | HTTP 422; OpenAPI enum omits it. |
| v3 consumes strategy `execution` (`max_entry_wait_bars`, `on_unfilled_entry`) | Could not be exercised. Strategy *declares* `maker_only` / wait 3 / `reprice`. v1/v2 docs say those fields are ignored. |
| Do not reinterpret v1/v2 | Ops agent refused to submit v1/v2 and call it maker. |

`submit-backtest` **did** print the HTTP 422 body. `save-draft` **did not**. Inconsistent CLI failure surfaces; see §7.

### Slice 4 — paper/live PnL from the fill ledger

Never reached. No paper fills, so `_deployment_performance` / `thytrader-operator performance --deployment-id` could not be judged. The user explicitly said: if PnL is missing or timeframe is still `1h`, that is a bug. Here the bug was earlier: **paper would not start at all** on a 5m fingerprint.

Prior ETH 5m library payload showed `"paper_live": {"paper": "unavailable", "live": "unavailable"}` — consistent with “no 5m paper on this image,” not with “paper exists but PnL is a fill-count slice.”

### Slice 1 last — 5m paper, not live (ADR 0018)

| Acceptance | This run |
|---|---|
| Paper may start a published `5m` strategy | HTTP 409, detail requires `1h`. Same string as the plan’s pre-change test `test_five_minute_strategy_cannot_start_paper`. |
| Live stays 1h | Not attempted. Must still 409 after rebuild. |
| Worker evaluates closed 5m bars | Unreachable. |

`thytrader-runtime` skill on disk (HEAD) says paper may be 1h or 5m. The process that served `/api/v1/deployments` still implements the old guard. Skill text is not a runtime contract.

---

## 6. Strategy expressiveness (what the product can actually say)

This is as important as the stale image. The user asked for an *invented* 5m strategy inside the schema. Several “obvious” 5m ideas are **illegal on the running API** even after you rebuild slices 1–4.

Running indicator catalog (`GET /api/v1/operator/indicators`): `ema`, `sma`, `rsi`, `atr`, `volume_sma` only. Period bounds match the skill.

Running schema tighter than some docs:

| Docs / intuition | Running OpenAPI / 422 |
|---|---|
| Sizing `fixed_quote` | Only `RiskFractionSizing` |
| Stop `percentage` | Only `AtrMultipleStop` |
| Take-profit `percentage` | Only `RewardRiskTakeProfit` |
| Trailing anything | Only `DisabledTrailingStop` (`enabled: false`) |
| Condition operand `close` / raw `volume` | Operands are `indicator` or `literal` only |
| `crosses_above` / `crosses_below` vs a level (`RSI` through 40) | **Rejected:** crossover right operand must be an indicator |
| Paper/live still 1h (canonical-strategy-schema.md field table) | Stale vs ADR 0018; **matches this API** |

Intended idea that failed validation: RSI bounce through 40 while in an SMA uptrend.

Legal alternative that published:

- Trend: `sma_1h` (SMA 12) `greater_than` `sma_8h` (SMA 96). There is no close-vs-SMA operand; two SMAs is the substitute.
- Trigger: `ema_fast` (EMA 8) `crosses_above` `sma_1h` (reclaim).
- RSI filter: `>= 40` and `< 70` (comparisons to literals are fine; crossovers against literals are not).
- Volume: `vol_1h` (volume SMA 12) `greater_than` `vol_4h` (volume SMA 48). There is no “this bar’s volume vs volume SMA” without a volume *level* indicator.
- Stop ATR×1.5, TP 1.5R, trailing off, time exit 36 bars (3 hours on 5m), cooldown 12 bars.
- Execution: `maker_only`, `max_entry_wait_bars: 3`, `on_unfilled_entry: reprice`.
- Warmup 120 (covers SMA 96 and RSI `period+1`).
- Sizing: risk fraction `0.005`, min quote `10`, max quote `100`, exposure `0.10`, one position.

That execution block is what v3/paper are supposed to consume. On this image, publication stores it and **v1/v2 would ignore wait/reprice.** If you ship v3 but the browser support matrix still says v1/v2 ignore those fields, update the matrix in the same change or agents will keep researching the wrong engine.

`create-draft --timeframe 5m` still seeds the **EMA(20)/EMA(50)/RSI≥50** reference. Fine as a template; the `--help` line “Paper/live stay 1h” is a lie relative to ADR 0018 and a truth relative to this API. Pick one and make CLI help, OpenAPI, skills, and `create_deployment` identical.

---

## 7. Contract contradictions the ops agent had to hold in its head

These are copy-paste conflicts across skills, CLI help, OpenAPI, ADRs, and HTTP. They caused more delay than Coinbase.

| Surface | What it says | What HTTP did |
|---|---|---|
| `skills/thytrader-runtime/SKILL.md` (HEAD) | Paper may start on closed 1h **or 5m**; live 1h only | 409 5m paper |
| `skills/thytrader-data/SKILL.md` | “Paper and live stay on 1h.” Forbidden: “Arming 5m paper or live trading” | Matches this API; contradicts runtime skill and ADR 0018 |
| `skills/thytrader-research/SKILL.md` | “Paper and live deployments still require 1h.” | Matches this API |
| `thytrader-research create-draft --help` | “Paper/live stay 1h.” | Matches this API |
| `docs/architecture/canonical-strategy-schema.md` timeframe row | “Paper and live still require 1h.” | Stale vs ADR 0018 |
| ADR 0018 / plan | 5m paper allowed | Not in this image |
| ADR 0016 / data skill | 2160h 5m lookback, classify holes across it | Watch 2160, ingest 4032, inspect-gaps 14-day window |
| ADR 0017 / backtest-simulation.md | v3 resting maker | OpenAPI + 422 only v1/v2 |
| Operator stale-image rule | version mismatch or 404-while-ready | Neither; both sides `0.1.0` |
| `save-draft` CLI | redacted “failed safely” | Real bug was 422 crossover rule |
| `submit-backtest` CLI | prints `HTTP 422: {…}` | Usable |
| Instance `health` vs `runtime`/`risk` | health green | runtime/risk degraded for missing registry |

**Do not leave data skill forbidding 5m paper while runtime skill allows it.** An ops agent must follow both. The data skill’s “never arm 5m paper” reads as a hard stop even when the user and ADR 0018 ask for 5m paper. Align the skills in the same PR as `create_deployment`, or the next ops agent will refuse a legal paper start.

---

## 8. Suggested fixes (for the implementing agent)

Priority is “what blocked a competent ops agent with user permission,” not code style.

### 8.1 Make stale Compose detectable without reading git log

`application_version: 0.1.0` on both CLI and API is why `make run` was correctly withheld and the journey still failed.

Concrete options (pick one, document in operator skill + `ops/README.md`):

1. **Content identity in health.** Include git commit, image digest, or a monotonic `SCHEMA_VERSION` / feature set (`supports_5m_paper`, `max_5m_historical_intervals`, `backtest_engines: [v1,v2,v3]`) in `thytrader-operator health` and `/health/ready`. CLI compares to its own supported set and prints the existing “API version does not match the CLI” rebuild signal.
2. **Bump `application_version` on the 5m-research-paper merge** (and any future ops-contract change). A string compare would have fired.
3. **Fail closed on unknown engine / timeframe in the CLI before HTTP** *and* tell the operator “running API is older than this CLI; rebuild with `make run`.” Today the CLI sent v3, the API 422’d, and health stayed green.

Until (1) or (2) exists, every ops agent will either (a) refuse to rebuild and stop, as this one did, or (b) rebuild without authorization. Both are bad. The product promised a rebuild signal; it did not emit one.

Also expose whether migration `0016` is applied in a **redacted** operator field. `ops/README.md` already tells agents to apply it on rebuild; they cannot verify it through the skill surface.

### 8.2 Do not report a 14-day 5m island as complete for a 90-day watch

Catalog `complete: true` + `lookback_hours: 2160` + `expected_candle_count: 4032` taught the agent the job was done.

After ADR 0016 is actually in the process:

- `expected_candle_count` for a 2160h 5m watch must be the lookback-sized grid (or the classified-complete subset must not be labeled as covering the watch).
- `inspect-gaps` **must** list the missing prefix/suffix/holes over `[now-lookback, now)` with `not_fetched` / `exchange_unavailable` / `incomplete_local`. A 14-day island inside a 90-day watch is **not** `gap_count: 0` for the watch.
- Operator `data-catalog` / `market-data` should distinguish **island completeness** from **watch completeness**. Fresh + gap-free island ≠ “we have months.”
- Ingest CLI returning in ~2s with 4032 bars should be treated in tests as a regression against 90-day backfill, not a success.

Integration test the ops agent actually ran:

1. `watch-add BTC-USD 5m --lookback-hours 2160 --confirm`
2. `ingest --confirm` and wait until the ingest flag clears (minutes, not 2s, for a real 90-day Coinbase walk).
3. `inspect-gaps` either (a) shows classified holes with causes and `interpolated: false`, or (b) shows coverage on the order of tens of days, not `4032` with empty `gaps`.
4. Catalog fingerprint is bound; no interpolation fields anywhere.

### 8.3 OpenAPI, submission, and CLI must name v3 together

A HEAD CLI + old API is inevitable during rolling rebuilds. Minimum:

- Running `/openapi.json` enum includes `thytrader-bar-backtest-v3` **iff** the kernel implements it.
- 422 message should say the running engine set, not only “v1 or v2,” once v3 exists.
- Browser research UI engine picker and the “v1/v2 ignore maker wait” inspector matrix must list v3 as the paper-comparable contract.
- Never silently map v3 → v1.

Ops golden request (after rebuild) — same JSON as §4.4, dataset fingerprint taken from **current** catalog, not this report’s stale 4032 hash.

Assert on the result document, not only HTTP 200:

- `engine_contract_version == thytrader-bar-backtest-v3`
- broker block is post-only / `resting_limit` / `bar_extreme` / `last_close` (ADR 0017)
- `bar_execution.fill_timing == resting_maker_limit` (or whatever the canonical literals are)
- entry fees use **maker** rate; unfilled wait/reprice appears in the ledger when bars do not trade through the rest
- result fingerprint stable on identical resubmit (idempotent)

### 8.4 5m paper 201; live 5m still 409; performance clock from the strategy

Ops golden:

```text
thytrader-runtime start --strategy-fingerprint sha256:… --mode paper --cash 10000 --confirm
→ 201, mode=paper, status running/starting, strategy_fingerprint echoed
```

Then immediately:

```text
thytrader-operator runtime --deployment-id <uuid>
thytrader-operator performance --deployment-id <uuid>
```

Must **not** hardcode `timeframe: "1h"`. Must be `5m` from the published definition. `total_net_pnl` may be `0` / flat with no fills; it must **not** be `null` with `RUNTIME_SLICE` if the ledger exists and there is no open unmarked inventory. `MISSING_MARK` is acceptable only with open inventory and no last close.

Live start on the same 5m fingerprint must remain 409 **after** the paper fix. Add that to the same test module so it cannot regress independently.

Worker: `new_closed_bars` must step 5 minutes, not snap to UTC hours. The ops user called out “if the timeframe is still 1h, that is a bug” because a previous 5m paper start that “succeeds” but evaluates hourly is worse than a 409.

### 8.5 CLI error quality is part of the agent contract

`save-draft` hid a precise 422. The ops agent only diagnosed it by raw `PUT`. That is the skill saying “prefer the CLI” fighting a CLI that deletes the body.

- `save-draft`, `publish`, `submit-backtest`, `watch-add`, `ingest`, `start` should print the HTTP status and redacted `detail` JSON on 4xx/409, the way `submit-backtest` already did for v3.
- For strategy 422s, prefer the **first semantic validator message** (`crossover right operand must reference an indicator`) over Pydantic union explosions.

### 8.6 Align skills, help, and schema docs in the same change as the API

If you ship 5m paper without editing `thytrader-data` / `thytrader-research` skills and `create-draft --help`, the next ops agent will stop at “paper stays 1h.”

Checklist:

- [ ] `skills/thytrader-data/SKILL.md` — 5m paper is runtime’s job; data skill should not forbid it. Keep “no live 5m” and “never interpolate.”
- [ ] `skills/thytrader-research/SKILL.md` and `create-draft --help` — paper may be 5m; live 1h.
- [ ] `docs/architecture/canonical-strategy-schema.md` timeframe row.
- [ ] `thytrader-runtime` skill vs `create_deployment` vs OpenAPI (already aligned on HEAD text; must match HTTP).
- [ ] Operator performance docs: paper/live timeframe from published strategy; fill-ledger PnL; `MISSING_MARK`.
- [ ] Stale-image rule: include feature/commit mismatch, not only 404/semver.

### 8.7 Schema: document crossover-vs-literal in the skill, not only in a 422

Research skill should say: `crosses_above` / `crosses_below` require **two indicators**. RSI/SMA *levels* use `greater_than*` / `less_than*` against `literal`. That one line would have saved a failed `--confirm` mutation and a hidden CLI error.

Optional later (out of this plan): a constant/level indicator or an explicit `threshold` operand so “RSI crosses 40” is sayable. Do not approximate it by inventing a fake indicator series.

### 8.8 Rebuild is required for *this* machine, and it is not optional for QA

This instance cannot validate HEAD until `make run` from repository root (and migration 0016, per `ops/README.md`). After rebuild, a developer or ops agent should re-run **the same user journey**, not only pytest:

1. Health includes a mismatch signal if you forget to rebuild.
2. BTC-USD 5m watch 2160 → ingest until catalog/inspect-gaps reflect months **or** classified holes.
3. Publish (or reuse) a 5m strategy with `maker_only` + wait + reprice.
4. `submit-backtest` v3 on the **latest** dataset fingerprint; omit window; record result fingerprint.
5. Paper start on that strategy fingerprint, cash `10000`.
6. Operator runtime + performance: `timeframe=5m`, PnL fields from the ledger.
7. Live 5m still 409.
8. Confirm `inspect-gaps.interpolated === false` throughout.

Pytest of kernel v3 and `test_five_minute_strategy_cannot_start_paper` flipping to paper-201/live-409 is necessary and **not sufficient**. This report exists because in-process tests can be green on HEAD while Compose still serves the 13:10 image.

---

## 9. Artifact index (this run)

Use these to avoid inventing identities. Dataset fingerprints **age**; re-read catalog before a new backtest.

| Kind | Value |
|---|---|
| Strategy id | `01a09294-0d4a-73eb-9d49-c872e56b8a70` |
| Strategy name | BTC 5m SMA-trend EMA reclaim |
| Timeframe | `5m` |
| Product | `BTC-USD` |
| Published fingerprint | `sha256:32a6b0a62c329a37e9d36541a34a588bde80172cbc442f479444a04b6862db80` |
| Draft revision after save | 2 |
| First 5m dataset fingerprint | `sha256:4e7c13c1d431e86387bd97928c86050d2b2e36e901bbda1d5c2cbe377443fb5f` (4032 bars) |
| Later 5m dataset fingerprint | `sha256:d9d0adea3178da7e614cbb58ec300a289830b710cb5d34d832f1030da4c2cab1` (4033 bars) |
| 5m island start | `2026-08-28T22:20:00Z` |
| v3 backtest | **not created** (422) |
| Paper deployment | **not created** (409) |
| Prior ETH 5m fingerprint (unrelated) | `sha256:f34c54431f9a7a50d420b233b45a290621c8baa2c6e6abe904312d1aff24cce8` |
| Prior ETH 5m v1 result | `sha256:f3593f0a6768b61187bfd42e109c8b9b387df130a858c95200a01dc50e522ad5` |

GET source: `/api/v1/strategies/source/sha256:32a6b0a62c329a37e9d36541a34a588bde80172cbc442f479444a04b6862db80`

---

## 10. What the ops agent refused to do (keep these forbidden)

These were tempting workarounds. They would have poisoned the evidence you need:

- `make run` without the documented stale-image signal (version still `0.1.0`).
- `--local` research to run HEAD’s v3 kernel against PostgreSQL while HTTP paper still 409s — mixed clocks, mixed images.
- Submit `thytrader-bar-backtest-v1` or `v2` and present PnL as maker-comparable.
- Start 1h paper on a different published fingerprint.
- Interpolate or stitch 1h BTC into 5m.
- Grep/patch `src/` or make the API dataset volume writable.
- Scrape worker logs or query PostgreSQL to “see if 90-day ingest is still going” after the CLI said `succeeded` / `complete: true`.
- Arm live, or treat `transfer` permission as live consent.

---

## 11. Bottom line for development

HEAD’s plan and ADRs 0016–0018 describe the product the user asked to operate. The **running Compose API at 13:10 MDT did not**. Health stayed green. Completeness stayed green on a 14-day 5m clip. v3 was not in OpenAPI. 5m paper still 409’d with the old test string.

Until health/CLI can say “this API is older than this CLI / this feature set,” ops agents will keep stopping on a healthy instance that cannot perform the documented journey.

After you rebuild this machine, the acceptance bar is not “tests pass.” It is: **the same skill sequence, on loopback HTTP, produces months-or-classified 5m history, a v3 result fingerprint, and a 5m paper deployment whose operator performance clock is 5m and whose PnL comes from the fill ledger.**
