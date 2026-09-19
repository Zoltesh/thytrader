---
name: thytrader-runtime
description: >-
  Start, pause, resume, or stop ThyTrader paper and live deployments, publish
  the risk-policy registry, and show/set/clear write-only Coinbase credentials,
  through the confirmation-gated thytrader-runtime CLI. Use when the user
  explicitly asks to deploy, pause, resume, stop, place an on-demand order, set
  the risk policy, or manage Coinbase API secrets. Requires --confirm on every
  mutation unless YOLO covers that tier. Live start and live place-order also
  require --i-understand-live. YOLO live may skip --confirm on start/pause/resume/stop
  only. Credential set/clear always need --confirm; YOLO never covers them.
  Publishing a risk policy or setting credentials does not arm live trading.
  Never diagnose through this skill and never submit Coinbase orders directly.
---

# ThyTrader runtime

Confirmation-gated paper and live **control**, including the risk-policy registry and write-only
Coinbase Advanced Trade credentials. This skill is not an extension of `thytrader-operator` or
`thytrader-research`.

HTTP-only against the loopback API (`THYTRADER_API_BASE_URL` or `http://127.0.0.1:8200`). There is no `--local` database mode.

Production installs enforce the application trust boundary
([ADR 0061](../../docs/decisions/0061-application-trust-boundary.md)): HTTP mutations need
`Authorization: Bearer <installation-token>` from `THYTRADER_INSTALLATION_TOKEN` or
`$THYTRADER_CREDENTIALS_DIR/.installation-token` ([ADR 0070](../../docs/decisions/0070-mutation-cli-installation-auth.md)
documents the shared helper used by every mutation lane). Browser mutations additionally require CSRF
from `GET /api/v1/security/session`. Live arming still requires a published risk policy per
[ADR 0063](../../docs/decisions/0063-stage-5-release-discipline-ci-risk-defaults-rate-budget.md)
plus `--i-understand-live`; do not expect a separate live-arm token endpoint.
Protection, leases, and live capital follow
[ADR 0058](../../docs/decisions/0058-protection-lifecycle-accounting.md): pause
(`lifecycle_command=stop_new_entries`) still maintains verified attached-child protection on
every worker poll, including empty `due` and feed-down. Default stop is managed shutdown
(protective brackets and residual occupancy stay in account risk). `--flatten` /
`POST /api/v1/deployments/{id}/stop?flatten=true` marketably exits then cancels remainders.
Live sizing uses allocated capital or venue available quote, never ledger `cash`. Workers hold
a 45s fenced lease; writes are revision-checked. UTC day-open and high-water baselines persist
across pause. Discover lease/lifecycle/latch fields on `thytrader-operator runtime` and capital
on `thytrader-runtime show`.

In-app operator chat (`/chat`, `/api/v1/operator-chat`) may invoke these same HTTP routes. It is
not extra authority: mutations still need in-app confirmation, and live still needs understand-live.
Paper start and paper place-order tools may pass optional `maker_fee_rate` / `taker_fee_rate`
together ([ADR 0048](../../docs/decisions/0048-paper-deploy-fee-fields.md)); live rejects them.
`runtime_set_risk_policy` publishes the same `PUT /api/v1/risk-policy` document as this CLI,
including daily-loss / drawdown / rate / collar fields
([ADR 0050](../../docs/decisions/0050-daily-loss-drawdown-rate-collars.md))
and `allow_intra_strategy_pyramiding`
([ADR 0056](../../docs/decisions/0056-multi-instrument-documents-and-pyramiding.md)).
Coinbase secrets use `GET/PUT/DELETE /api/v1/credentials/coinbase`
([ADR 0053](../../docs/decisions/0053-workstation-ia-write-only-coinbase-credentials.md)); LLM
keys stay on `/chat` ([ADR 0051](../../docs/decisions/0051-in-app-operator-chat.md)).
Do not treat chat as this skill.

Live trading spends real money. Do not start live unless the user explicitly asked to arm live trading.

Paper may start on closed **venue-clock** bars of a published strategy (`1m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, or `1d`). Live may start on the same clocks. Sub-hour live pauses unless the user-order feed is connected. Published `htf_filter` and optional per-indicator extra timeframes evaluate last-completed complete-only bars; missing extra-TF or HTF coverage pauses. Paper and live do not bind frozen extra-TF or HTF dataset fingerprints. Ingest those extra clocks with `skills/thytrader-data/SKILL.md` before start. `place-order --timeframe` is the discretionary book clock (default `5m`; any ingested venue clock).

## Hard stop

When operating a running instance, do not edit `src/`, `compose.yaml`, Dockerfiles, Alembic, or tests.
Do not search the tree for a code patch. Report failures through this skill. Every command preflights
the full `/health/ready` ops contract. Rebuild or restart only with `make run` when the user asked,
or when the CLI reports a version or ops-contract mismatch, or HTTP 404 on an agent route while
`/health/ready` is 200 (the shared stale-image signal). Matching `0.1.0` alone is not current-image
evidence. Open the `ops/` workspace instead of the git root. Run every
`uv run thytrader-*` command from the repository root (the parent of `ops/`).

## Commands

| Need | Command |
|---|---|
| List deployments | `uv run thytrader-runtime list` |
| Show one snapshot | `uv run thytrader-runtime show UUID` |
| Start paper | `uv run thytrader-runtime start --strategy-fingerprint sha256:… --mode paper --cash 10000 --confirm` |
| Start paper with fee assumptions | `uv run thytrader-runtime start --strategy-fingerprint sha256:… --mode paper --cash 10000 --maker-fee-rate 0.001 --taker-fee-rate 0.002 --confirm` |
| Start live | `uv run thytrader-runtime start --strategy-fingerprint sha256:… --mode live --confirm --i-understand-live` |
| Pause | `uv run thytrader-runtime pause UUID --confirm` |
| Resume | `uv run thytrader-runtime resume UUID --confirm` |
| Stop (managed shutdown) | `uv run thytrader-runtime stop UUID --confirm` |
| Stop and flatten | `uv run thytrader-runtime stop UUID --flatten --confirm` ([ADR 0058](../../docs/decisions/0058-protection-lifecycle-accounting.md)) |
| Clear latched breakers | `uv run thytrader-runtime reset-breaker-latches UUID --confirm` ([ADR 0064](../../docs/decisions/0064-deployment-http-lifecycle-and-breaker-latch-reset.md)) |
| Place paper long | `uv run thytrader-runtime place-order --mode paper --product-id BTC-USD --timeframe 5m --side long --origin agent --entry-kind post_only_limit --limit-price 100000 --quantity 0.01 --stop-price 90000 --take-profit-price 120000 --idempotency-key KEY --cash 10000 --confirm` |
| Place paper short | `uv run thytrader-runtime place-order --mode paper --product-id BTC-USD --timeframe 5m --side short --entry-kind post_only_limit --limit-price 100000 --quantity 0.01 --stop-price 110000 --take-profit-price 90000 --idempotency-key KEY --cash 10000 --confirm` |
| Place live long | `uv run thytrader-runtime place-order --mode live --product-id BTC-USD --timeframe 1h --entry-kind marketable --quantity 0.01 --stop-price 90000 --take-profit-price 120000 --idempotency-key KEY --confirm --i-understand-live` |
| Place live short | `uv run thytrader-runtime place-order --mode live --product-id BTC-USD --side short --entry-kind marketable --quantity 0.01 --stop-price 110000 --take-profit-price 90000 --idempotency-key KEY --confirm --i-understand-live` |
| Show risk policy | `uv run thytrader-runtime show-risk-policy` |
| Publish risk policy | `uv run thytrader-runtime set-risk-policy --quote-currency USDC --max-concurrent-running-deployments 8 --max-concurrent-open-positions 8 --max-portfolio-exposure-fraction 1 --per-product-max-exposure-fraction 1 --paper-capital-quote 100000 --confirm` |
| Publish risk policy with pyramiding | `uv run thytrader-runtime set-risk-policy --max-concurrent-running-deployments 8 --max-concurrent-open-positions 8 --max-portfolio-exposure-fraction 1 --per-product-max-exposure-fraction 1 --paper-capital-quote 100000 --allow-intra-strategy-pyramiding --confirm` |
| Publish risk policy with absolute caps and a venue budget | `uv run thytrader-runtime set-risk-policy --max-concurrent-running-deployments 8 --max-concurrent-open-positions 8 --max-portfolio-exposure-fraction 1 --per-product-max-exposure-fraction 1 --paper-capital-quote 100000 --max-daily-loss-quote 2500 --max-portfolio-exposure-quote 50000 --max-venue-order-actions-per-minute 90 --confirm` |
| Show YAML settings | `uv run thytrader-runtime show-settings` |
| Set YOLO paper without restart | `uv run thytrader-runtime set-settings --yolo-enabled true --yolo-tiers paper --confirm` |
| Show Coinbase credential flags | `uv run thytrader-runtime show-coinbase-credentials` |
| Set Coinbase credentials | `uv run thytrader-runtime set-coinbase-credentials --api-key-name organizations/…/apiKeys/… --private-key-file ./coinbase.pem --confirm` |
| Clear Coinbase credentials | `uv run thytrader-runtime clear-coinbase-credentials --confirm` |

`list`, `show`, `show-risk-policy`, `show-settings`, and `show-coinbase-credentials` are read-only and do not use `--confirm`. Optional
`--product-allowlist BASE-USD` and `--allocation STRATEGY_UUID:QUOTE` may be repeated.

`list` and `show` return `positions[]`, `instrument_runtimes[]`, product-tagged `orders`/`fills`,
`book_totals` (`open_books`, `working_orders`, `fill_count`) that must match those collections
([ADR 0060](../../docs/decisions/0060-multi-book-deployment-api.md)), the published strategy
`timeframe` (copied from the immutable strategy when the stored deployment row is null;
[ADR 0064](../../docs/decisions/0064-deployment-http-lifecycle-and-breaker-latch-reset.md)),
ADR 0058 lifecycle fields
(`lifecycle_command`, `daily_loss_latched`, `drawdown_latched`, `revision`, `worker_lease_held`;
[ADR 0064](../../docs/decisions/0064-deployment-http-lifecycle-and-breaker-latch-reset.md)), and a
`capital` block with `allocated_capital`, `venue_available_quote`, `reserved_buying_power`,
`inventory_cost`, `performance_equity`, `initial_equity`, `baseline_equity`,
`high_water_mark_equity`, and `utc_day_open_equity`
([ADR 0065](../../docs/decisions/0065-deployment-capital-accounting-http.md)). Top-level `cash` is
ledger fill accounting only; live sizing uses `capital.allocated_capital` or
`capital.venue_available_quote` (null when unknown). The singular `position` field is
compatibility-only (focused book, always includes `product_id` and `compatibility_focus`). Read
`positions` for inventory. `--i-understand-live` is unchanged. `set-risk-policy` optional breaker
flags default to the compiled envelope: `--daily-loss-limit-fraction 1`,
`--max-strategy-drawdown-fraction 1`, `--max-entry-orders-per-minute 60`,
`--max-cancellations-per-minute 60`, `--reference-price-collar-fraction 0.5`. Daily-loss and
drawdown trips pause risk-increasing orders (exits continue). Rate and collar denies do not pause.
Pass `--allow-intra-strategy-pyramiding` when paper/live same-side adds should be legal; the
published strategy must also enable `entry.pyramiding`. Omitted (false) keeps compiled-default
policy bytes stable. Schema-enabled pyramiding without this flag is denied
(`PYRAMIDING_NOT_ALLOWED`). Backtests follow the strategy document only. `set-risk-policy`
requires `--confirm` and does **not** require `--i-understand-live`. `reset-breaker-latches`
always requires `--confirm`; YOLO never skips it.

**Live start and live on-demand orders require a published policy** ([ADR 0063](../../docs/decisions/0063-stage-5-release-discipline-ci-risk-defaults-rate-budget.md)):
the compiled default is a wide **paper** research envelope, not a live-safe default. A fresh
install's `start --mode live` or `place-order --mode live` is denied
(`LIVE_REQUIRES_PUBLISHED_POLICY`) until `set-risk-policy --confirm` has published at least one
version. Paper is unaffected. Optional `--max-daily-loss-quote` and
`--max-portfolio-exposure-quote` add an absolute quote ceiling alongside the matching fraction
(whichever binds tighter trips first); unset by default, since this skill does not assert a
universal safe amount for every operator. Optional `--max-venue-order-actions-per-minute` adds a
combined cap across entry-order and cancellation requests in the same rolling minute — a coarse
venue-request budget that can only ever deny a **new entry**, never a cancellation or protective
(stop/take-profit/time-exit/bracket) submission, since only new-entry admission calls this check.
`--max-entry-orders-per-minute` itself now counts only entry-purpose orders, so recent protective
activity no longer exhausts it and blocks an unrelated new entry.
`place-order` is confirmation-gated. Live place-order also requires `--i-understand-live`.
Optional `--note` is frozen onto the why-trade record at persist. Later review notes use
`thytrader-memory add-trade-reason-note --confirm` (YOLO never covers that lane).
`--side` defaults to `long`; pass `short` for a spot sell-to-open. Live shorts fail closed without
available base and never borrow. When SL/TP are known and trailing is off, live uses an
attached bracket on the entry; paper still uses synthetic exits. `--timeframe` defaults to `5m`; pass `1m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, or `1d` for
another book clock. Paper `start` and paper `place-order` accept optional `--maker-fee-rate` and
`--taker-fee-rate` together (Decimal strings in `[0, 0.1]`, maker ≤ taker). Omitted paper rates
use the documented `0.001` / `0.002` assumptions. They are **not** observed Coinbase fees. Live
rejects those flags; live fills stay venue-recorded through cursor-terminated List Fills
([ADR 0059](../../docs/decisions/0059-coinbase-list-fills-cursor-pagination.md)). Incomplete or
unparseable Coinbase fill pages fail closed (`BrokerError`); do not treat them as a complete
empty remainder. YOLO may skip `--confirm` for paper start/pause/resume/stop/place-order
when the `paper` tier is enabled, and for live start/pause/resume/stop when the `live` tier
is enabled. Live place-order, `set-risk-policy`, `set-settings`, and Coinbase credential set/clear
never skip `--confirm`. YOLO never covers credentials. Pass `--private-key-file`; never a CLI
secret or pasted PEM. Setting credentials does not arm live trading. Repeat the same
`--idempotency-key` instead of retrying a timeout.

Underlying HTTP:

- `GET /api/v1/deployments?limit=&offset=` (summary rows; no historical orders/fills)
- `GET /api/v1/deployments/{id}?detail=summary|full` (default `summary`)
- `GET /api/v1/deployments/{id}/fills?limit=&cursor=` and `/orders?limit=&cursor=`
- `POST /api/v1/deployments`
- `POST /api/v1/deployments/{id}/pause`
- `POST /api/v1/deployments/{id}/resume`
- `POST /api/v1/deployments/{id}/stop` (optional `?flatten=true`; default is managed shutdown)
- `POST /api/v1/deployments/{id}/reset-breaker-latches`
- `POST /api/v1/discretionary-orders`
- `GET/PUT /api/v1/risk-policy`
- `GET/PUT /api/v1/settings` (YAML non-secrets and YOLO; no secret echo; [ADR 0055](../../docs/decisions/0055-yaml-settings-runtime-reloadable-yolo.md))
- `GET/PUT/DELETE /api/v1/credentials/coinbase`

## Confirmation

- Never mutate unless the user explicitly asked **and** `--confirm` is present, unless the user
  explicitly asked to operate under YOLO **and** operator `configuration` /
  `thytrader-playbook status` shows the matching tier (`paper` or `live`) enabled.
- Never start live or place a live order without `--i-understand-live`. YOLO never skips that
  flag. Live start/pause/resume/stop may omit `--confirm` only when the `live` tier is enabled
  and the skip audit succeeds. Live `place-order`, `set-risk-policy`, `set-settings`, and Coinbase
  credential set/clear never YOLO.
- Fail closed if YOLO is off, the needed tier is absent, or the skip audit is unavailable.
  Do not retry with extra flags unless the user asked you to.
- Successful mutations print JSON identities (`id`, `mode`, `status`, `kind`, optional
  `strategy_fingerprint`). Keep those identities.
- Watch status after a mutation with `uv run thytrader-operator runtime --deployment-id UUID`.

YOLO on/off and independent tiers live in `thytrader.yaml` (loopback `/settings`, or
`set-settings --confirm`). Leftover `THYTRADER_YOLO_TIERS=paper` is valid. YAML wins leftover env
and applies without restart. Secrets stay out of YAML. Live still needs `--i-understand-live`.

## Forbidden

- Using this skill because you can observe a runtime
- Folding these commands into operator or research skills
- Printing API keys, private keys, `.env` values, or database URLs
- Direct PostgreSQL access
- Cancelling individual Coinbase orders
- Publishing a risk policy without `--confirm`, or treating that mutation as live arming
- Setting or clearing Coinbase credentials without `--confirm`, printing the PEM, or treating
  a credentials write as live arming
- Treating a timeout as proof the start/pause/stop/place-order failed; `show` the deployment and reconcile before retrying
- Editing application source to arm, pause, or change execution on a running instance
