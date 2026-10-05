# Research campaigns and economic checks

## Frozen campaigns, prospective validation, and economic preflight (ADR 0109)

Use the research HTTP lane for these commands; `--local` is not supported. Creation
freezes rules and authorizes research children only. It never deploys or cancels orders.

| Task | Command |
| --- | --- |
| Freeze a campaign | `uv run thytrader-research create-campaign --file campaign.json --confirm` |
| List campaigns | `uv run thytrader-research list-campaigns --limit 20` |
| Inspect frozen intent and child evidence | `uv run thytrader-research show-campaign --campaign-id UUID` |
| Advance approved research now | `uv run thytrader-research refresh-campaign --campaign-id UUID --confirm` |
| Export small result projections | `uv run thytrader-research export-results --limit 100 [--cursor CURSOR]` |
| Calculate order economics | `uv run thytrader-research economics --file economics.json` |

Campaign JSON has `name`, `kind: historical|prospective`, UTC `deadline`, `gates`, and
`cases: [{key, request: BacktestStartRequest}]`. Every case requires explicit
`evaluation_start` and `evaluation_end`; supply a `strategy_id` and the usual capital
and cost fields. Omitted datasets bind only once, when complete verified data can
cover the frozen window and warmup. Optionally set a case's `strategy_fingerprint`
to an already frozen snapshot of that same strategy; otherwise creation snapshots
its current valid rules. Later strategy edits never retune a campaign.

Gates are `minimum_trades` (default 10), `minimum_net_return_fraction` (default 0;
return must strictly exceed it), and `maximum_drawdown_fraction` (default 0.15).
A prospective case must begin at or after freezing. The existing research worker
advances approved campaigns automatically: `waiting_for_data` → `queued` → `running`
→ `passed`, `failed_gate`, `insufficient_sample`, `failed`, or `expired`. Passing is
research evidence, not permission to trade. Missing future candles never count as
validation; expired or undersampled cases never pass. Concurrent refreshes and
restarts queue one child identity per case. A failed child is retained; review it
before creating a replacement campaign.

Read/download one frozen report at `GET /api/v1/research/campaigns/{id}` and
`GET /api/v1/research/campaigns/{id}/export` (CSV). The UI is `/research`, linked from
Backtests, and offers campaign creation, status, CSV downloads, and economic preflight.

Economic preflight JSON uses `side`, `entry_price`, `quantity`, `stop_price`, optional
`target_price`, `maker_fee_rate`, `taker_fee_rate`, optional `fixed_slippage_bps` and
`spread_bps`. `POST /api/v1/research/economics` is read-only. It reports both entry and
exit fees, maker break-even, net TP return/PnL and stressed taker stop PnL. Supplied
fees are modeled assumptions, not a quote or permission to submit an order.
This read-only POST still requires installation authentication (and browser CSRF).
The CLI sends the credential automatically; calculation does not require `--confirm`.

An optional strategy field `entry.economic_guard: {minimum_net_target_return_fraction:
"0.002"}` uses the same net maker-target calculation in backtest, paper and live.
Absent means existing behavior and fingerprints remain unchanged. An enabled guard
requires a target and sufficient net return after both maker fees; it records
`NET_TARGET_BELOW_MINIMUM` without creating an order when refused. Live also requires
a current fee profile (`ECONOMICS_FEE_UNAVAILABLE` otherwise). Paper uses its declared
fee schedule. The guard does not bypass risk, venue sizing, or stop checks.
Replacement entries recheck the guard. Live repricing carries the observed fee tier;
a simulated reprice below the net hurdle expires the pending entry.

Backtests and studies accept optional `execution_stress` with `entry_latency_bars`
(additional completed bars before entry activation, 0–100), `maker_penetration_bps`
(0–1000, entries and targets), and `entry_fill_fraction` (>0 to 1). A partial entry
fills once at its posted limit and cancels the remainder. Latency does not consume
active entry wait; a reprice does not repeat activation latency. The profile is
fingerprinted in run costs and echoed in bounded results. These are deterministic
candle stresses, not observed order-book queue/latency or partial-fill reconstruction.
Omitting the profile preserves the original simulation semantics and fingerprints.

Default result summary reads and `export-results` verify publication/source digests
inside PostgreSQL without loading trade/equity arrays or historical Parquet.
`verification_scope: publication` explicitly distinguishes this from `detail=full`
artifact verification. Old publications may lack derived `metrics`; their warnings
say so. Fetch `/metrics` or the full result when that detail is needed. General bulk
export uses the existing offset cursor, so new concurrent publications can shift
pages; campaign reports pin child identities and are stable for that manifest.

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

## Closed-trade fee attribution

`cost_attribution` is a `thytrader-cost-attribution-v1` report outside canonical result
bytes. It contains `result_fingerprint`, `run_fingerprint`, its own
`attribution_fingerprint`, and `trade_count`. All amounts are exact decimal strings
in the strategy's quote currency:

- `fill_price_pnl_before_fees`: sum of recorded trade `gross_pnl`; modeled fill prices
  already include spread and slippage. Do not subtract either cost again.
- `entry_fees` and `exit_fees`: sums of recorded entry and exit fill fees.
- `net_pnl`: sum of recorded closed-trade net PnL.
- `accounting_residual`: net minus (before-fees PnL minus both fee totals).
- `summary_net_pnl_delta`: canonical summary net PnL minus closed-trade net PnL.

The simulator's Decimal64 arithmetic can leave tiny rounding differences; the last
two fields disclose them rather than attributing them to fees. Summary `gross_profit`
and `gross_loss` group winning/losing **net** trade PnL and are not before-fee totals.

`uv run thytrader-research show-result --result-fingerprint sha256:…` and
`export-results --limit 100 [--cursor CURSOR]` include this field. New publications
record it in Alembic 0064's nullable metadata column. Bounded reads do not load trade
or equity arrays: legacy missing metadata is `null` with a warning, never zero.
`GET /api/v1/backtests/{fp}?detail=full`, operator `performance --result-fingerprint`,
and explicit `show-result --local` compute it from fully read evidence. A verified
republish fills missing metadata without replacing recorded attribution or changing
result fingerprints. The result UI shows all four totals and reconciliation details.
No new CLI flags or trading authority are introduced.
