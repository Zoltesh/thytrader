# 0063: Stage-5 release discipline — tracked CI, production web target, live-arming gate, absolute risk caps, purpose-aware order-rate budget

- Status: Accepted
- Date: 2026-09-16
- Relates to: [0004](0004-safe-execution-and-access.md), [0012](0012-operator-diagnostics.md),
  [0033](0033-phase-10-risk-policy-registry.md), [0050](0050-daily-loss-drawdown-rate-collars.md),
  [0056](0056-multi-instrument-documents-and-pyramiding.md)

## Context

The reconciled 2026-09-16 external audit's stage-5 remediation row (F16, F25, F26, F35) grouped
release-discipline and hardening findings that do not require a new Alembic revision or ops-contract
bump:

- **F26** — no tracked GitHub Actions workflow verified the backend/frontend gates this repository
  already documents in `docs/contributing.md`. Latest-main verification also found five Prettier
  failures, one ESLint `svelte/prefer-svelte-reactivity` error, eight `npm audit` advisory nodes, and
  a Compose web process that still runs the Vite **development** server rather than a deliberate
  production target.
- **F16** — the Coinbase REST broker's `async def` methods (`place_order`, `cancel_order`,
  `get_order`, `list_fills`) called the synchronous SDK transport directly instead of offloading it,
  and `evaluate_and_publish_backtest` ran dataset reverification, signal-trace evaluation, and
  bar-level simulation synchronously inside the API's async request handler. Both can stall the
  event loop that also serves execution supervision, user-feed heartbeats, and operator control
  requests (pause/stop/status) on the same process.
- **F25** — the compiled default risk policy (100% exposure/loss/drawdown fractions, no absolute
  quote ceiling) is a permissive **research** envelope that a fresh, credentialed install could
  otherwise use to arm live trading without the operator ever reviewing or publishing a concrete
  policy.
- **F35** — `max_entry_orders_per_minute` counted every recent order regardless of purpose, so
  protective (stop/take-profit/time-exit/bracket) submissions could exhaust the same budget as new
  entries and deny an unrelated, legitimate entry. There was also no combined per-minute ceiling
  across every order-mutating venue request (entries, cancels, replacements).

Two constraints bound every fix below. First, another workstream is concurrently rewriting
`execution/loop.py`, `execution/reconcile.py`, and `execution_worker/service.py`; this slice stays
out of those files and defers any residual blocking work that lives only there. Second, the prior
audit's own adjudication (`C-4`) explicitly rejects treating an arbitrary "safe" loss/exposure
percentage as a fact independent of the operator's capital, product, and tested strategy. This ADR
does not invent a new universal fraction; it adds a structural gate and optional operator-set
absolute caps instead.

## Decision

### CI (F26)

Add `.github/workflows/ci.yml` with two jobs:

- **backend** — `uv sync`, boots a `postgres:17` service container, runs Alembic to `head`, then runs
  `uv run pytest` with `THYTRADER_TEST_DATABASE_URL` and `THYTRADER_INTEGRATION_DATABASE_URL` set so
  the PostgreSQL-backed suites execute rather than skip, followed by `uv run ruff check .`,
  `uv run ruff format --check .`, and `uv run ty check`.
- **frontend** — `npm ci`, `npx playwright install --with-deps chromium`, `npm run lint`,
  `npm run check`, `npm run test` (unit + Playwright browser suite against the dev server, matching
  local iteration), then `npm run build` (the production adapter-node build) and `npm audit
  --audit-level=high` as an informational, non-blocking dependency-drift signal.

Both jobs must pass on every pull request and on `main` before this ADR's remediation is considered
closed as a release gate, not merely as a local habit.

### Production web target (F26 / M-14)

Replace `@sveltejs/adapter-auto` with `@sveltejs/adapter-node`, matching the existing self-hosted
Docker Compose deployment model (native processes are the other supported target and need no
adapter). `web/server.js` wraps the compiled `build/handler.js` with the same `/api` reverse proxy
the Vite dev server already performs in development, driven by the existing
`THYTRADER_API_PROXY_TARGET` environment variable so no new configuration surface is introduced.
`web/Dockerfile` becomes a two-stage build: `npm ci && npm run build` in the builder stage, then
`npm ci --omit=dev` plus the compiled `build/` output and `server.js` in the runtime stage, started
with `node server.js`. Compose's published port, health check, and environment variables are
unchanged.

### Frontend lint and dependency triage (F26)

- Ran `prettier --write` on the five files the audit named
  (`audit.ts`, `research-studies.ts`, `strategy-diff.ts`, `strategy-insight.ts`,
  `routes/strategies/[id]/+page.svelte`); no runtime behavior changed.
- `YamlSettingsPanel.svelte`'s transient `new Set(...)` becomes `new SvelteSet(...)` from
  `svelte/reactivity`, satisfying `svelte/prefer-svelte-reactivity` without changing the toggle
  logic.
- `npm update` (within existing `package.json` semver ranges) resolved seven of the eight advisory
  nodes: the SvelteKit content-negotiation ReDoS, the `@vitest/mocker` path-traversal chain, and the
  `brace-expansion`/`nanoid` transitives. The remaining low-severity `cookie <0.7.0` advisory is
  pinned by `@sveltejs/kit@2.70.3` itself (`cookie: ^0.6.0`) and has no newer 2.x release yet; a
  package.json `"overrides": {"cookie": "^0.7.2"}` forces the patched transitive version. `npm audit`
  reports zero vulnerabilities after these changes.

### Bounded async offloading (F16)

Within `exchanges/coinbase_broker.py` (not `execution/loop.py`): every blocking
`self._transport.get`/`.post` call inside the already-`async def` methods (`place_order`,
`cancel_order`, `get_order`, `list_fills`, and the internal `_venue_id_for_client` pagination helper,
which becomes `async def` since its only caller is `get_order` in the same file) now runs through
`await asyncio.to_thread(...)`, matching the `asyncio.to_thread` convention already used by the
account and market-data adapters (`exchanges/coinbase.py`, `exchanges/coinbase_market_data.py`).
Within `backtest/service.py`: `evaluate_and_publish_backtest` now awaits the durable I/O
(`run_store.load`, `strategy_store.load`, `result_store.publish`) directly on the event loop, but
runs dataset reverification, signal-trace evaluation, and `simulate_backtest` — all synchronous
CPU-bound work — through a single `asyncio.to_thread(_load_and_simulate, ...)` call.

**Deferred, not fixed here:** `CoinbaseRestBroker.best_bid`/`.best_ask`/`.maker_limit_price` stay
synchronous. They are called from `execution/loop.py` (`_reprice_entry`, `_submit_sized_entry`)
through the `Broker` Protocol's sync `maker_limit_price` method; making them non-blocking needs an
async Protocol method and updated call sites in the file the other workstream owns. `list_spot_orders`
has no production caller today (diagnostics-only, exercised only by sync tests) and stays
synchronous rather than churning its call signature for no reachable benefit. A durable job-queue
for large research studies (returning a job id instead of blocking the request) remains a larger,
separate follow-up; this slice only bounds the bounded bar-level simulation work already reachable
from one API request.

### Live requires a published risk policy; optional absolute caps (F25)

`RiskPolicySource.COMPILED_DEFAULT` cannot arm a live deployment. `evaluate_new_deployment` (used by
both strategy deploys in `execution/service.py` and on-demand discretionary books in
`execution/discretionary.py`) takes a `policy_source` argument and denies
`DeploymentMode.LIVE` with the new `LIVE_REQUIRES_PUBLISHED_POLICY` reason code whenever the active
policy is still the compiled fallback. Paper starts are unaffected under any source: paper is the
research/practice venue and this ADR does not restrict it. An operator who has never published a
policy sees every live start denied with an actionable reason until they publish one via
`thytrader-runtime set-risk-policy --confirm` (`PUT /api/v1/risk-policy`).

`RiskPolicyDefinition` gains three new, all-optional fields (omitted from canonical JSON and the
fingerprint when unset, so every previously published policy's identity is unchanged):

- `max_daily_loss_quote` — an absolute quote ceiling enforced in addition to
  `daily_loss_limit_fraction`; the breaker trips at whichever bound is tighter.
- `max_portfolio_exposure_quote` — the same pattern for `max_portfolio_exposure_fraction`.
- `max_venue_order_actions_per_minute` — see F35 below.

No field carries a non-`None` default: this ADR does not assert a universal safe quote amount any
more than the prior audit's own review accepted a universal safe percentage. The operator sets a
cap that matches their own capital and tested strategy; the compiled fractions stay wide paper
research defaults.

### Purpose-aware order-rate accounting and a combined venue budget (F35)

`risk/breakers.py`'s `_rate_verdict` now resolves each recent order's purpose from
`DeploymentSnapshot.intents` (`Order.intent_id` → `OrderIntent.purpose`), matching the pattern
`execution/loop.py` already uses for `_active_entry`. Only `IntentPurpose.ENTRY` orders consume
`max_entry_orders_per_minute`; a snapshot without any intents (an ad-hoc or degraded snapshot,
never a real store-backed one — persistence always loads intents alongside orders) fails closed by
treating every order in it as a candidate entry, rather than silently exempting unknown orders from
the cap.

`max_cancellations_per_minute` keeps counting every cancellation regardless of purpose (protective
replacement activity is exactly what should be visible on that count). Because this whole function
is only ever invoked from `evaluate_new_entry`/`evaluate_rate_and_collar` while admitting a **new**
risk-increasing entry, and never while cancelling or submitting a protective order, both caps can
only ever block a new entry, never a risk-reducing action — the caller graph itself, not a special
case in this function, is what "reserve capacity for risk-reducing actions" means here.

The new optional `max_venue_order_actions_per_minute` counts every entry-purpose order creation plus
every cancellation together across the occupied books passed in (already portfolio/mode-scoped, i.e.
one venue for live). When set, it denies a new entry once the combined recent request volume reaches
it — a coarse, conservative proxy for the real Coinbase per-venue REST budget the transport layer
does not otherwise schedule. Unset by default; this repository does not assert a specific number as
Coinbase's actual documented limit.

**Deferred, not fixed here:** a true application-wide venue request scheduler/backoff contract
(H-8) — bounded retries for observation-only requests, explicit 429/`Retry-After` handling, and
never blindly retrying an ambiguous order-creation POST — needs transport-layer changes beyond this
policy-module slice and is left as a follow-up alongside the F16 broker work above.

## Consequences

- CI now enforces the checks `docs/contributing.md` already documented as the native quality gates,
  including the PostgreSQL-backed suites that previously ran skipped unless a contributor happened
  to export both database URLs locally.
- Compose serves the same compiled artifact `npm run build` already produced; the dev server is no
  longer the production web process.
- A fresh, credentialed install cannot place a live order — through a published strategy deploy or
  an on-demand discretionary book — until the operator publishes an explicit risk policy. Existing
  published policies are unaffected; only the compiled-default fallback is blocked from arming live.
- Absolute quote caps and the combined venue budget are opt-in. An operator who sets neither sees
  identical behavior to before this ADR, aside from the live-publication gate.
- Legitimate protective order activity (stop/target submission, bracket replacement) no longer
  erodes the entry-order rate budget, so it can no longer indirectly deny an unrelated new entry.
- `best_bid`/`best_ask`/`maker_limit_price` remain blocking on the live path; a large research study
  still runs synchronously relative to its own request even though it no longer blocks the shared
  event loop. Both are explicitly flagged above rather than silently left unaddressed.

## Alternatives considered

- **Rewrite `execution/loop.py`'s broker calls to be fully async end-to-end:** rejected for this
  slice; that file is owned by a concurrent workstream and the conflict is unavoidable without
  duplicating their in-flight rewrite. Left as an explicit deferred item instead.
- **Pick a new, tighter compiled-default percentage for live ("2% daily loss," etc.) instead of a
  publication gate:** rejected; the prior audit's own C-4 adjudication already rejected treating any
  single percentage as a universal fact, and a different arbitrary number would repeat that error
  under a different guise. A structural requirement to publish an explicit, reviewed policy avoids
  asserting a number this repository cannot justify for every operator and strategy.
- **Build a durable job-queue (job id + polling) for backtest/study submission:** rejected for this
  slice as disproportionate to a bounded blocking-call fix; `asyncio.to_thread` bounds the concrete
  regression (API responsiveness during one large run) without a new persistence/worker surface.
  Left as a larger follow-up if study sizes grow enough to need it.
- **Drop `max_cancellations_per_minute` entirely since cancellations are risk-reducing:** rejected;
  the audit's own fix text asks for a distinct "cancellation operational budget," and the existing
  cap already only ever gates a new entry, never the cancellation itself, so keeping it preserves a
  useful signal without contradicting the risk-reducing priority.
