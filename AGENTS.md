# AGENTS.md

## Mission

Build ThyTrader as a trustworthy, open-source, local-first **Coinbase-first research and trading
platform**: portfolio visibility, on-demand and strategy-driven execution, declarative strategy
design, reproducible backtesting, paper execution, and explicitly armed live Coinbase spot trading.
Other exchanges come later.

The **primary product surface is agent-driven end-to-end operation** (confirmation-gated skills and
versioned HTTP APIs). A modern professional UI still matters and must stay capable. See
[product vision](docs/product/vision.md), [ADR 0030](docs/decisions/0030-agent-e2e-primary-surface.md),
and [ADR 0031](docs/decisions/0031-coinbase-first-platform-end-state.md).

Financial correctness, restart safety, secret hygiene, and auditability outrank delivery speed.

Documentation map:

- `skills/thytrader-*/SKILL.md` + `references/`: canonical operator-agent docs (lane commands,
  gates, report fields). `ops/.cursor/skills/` symlinks into them.
- `AGENTS.md` (this file), `CLAUDE.md`, `.claude/skills/`, `.cursor/rules/`: contributor-agent docs.
- `docs/decisions/`: the ADR decision log ([index](docs/decisions/README.md)).
- `docs/architecture/`: overview, backtest simulation, research studies.
- `README.md` and `docs/user/`: the short human guide (setup, safety, browser operation, research).

## Current direction

- Backend: FastAPI and Python managed with `uv`.
- Frontend: SvelteKit/Svelte 5 with strict TypeScript.
- Runtime: modular monolith with separate API and continuously running worker processes.
- Initial exchange: Coinbase Advanced Trade REST v3 and WebSockets, spot only. Other exchanges later.
- Agent surface: primary product (HTTP-first skills/CLIs); SvelteKit UI remains required.
- Storage: PostgreSQL for operational state; Parquet with Polars/DuckDB for analytics.
- Deployment: Docker Compose for supported installs; native processes for development.
- Strategy model: one mutable declarative strategy per id (revision-guarded saves); backtests, studies,
  and paper/live runs bind an automatic content-addressed snapshot of the exact rules
  ([ADR 0082](docs/decisions/0082-strategy-root-mutable-strategies-auto-snapshots.md)).
- Access: loopback-only by default; remote exposure must be explicit and protected.

Accepted decisions live in `docs/decisions/`. Do not silently contradict an accepted ADR. Add a
superseding ADR and mark the old one's status (header and index) when direction changes; never
delete or rename an ADR.

## Known gaps

Open work, one line each (no roadmap file; history is in the ADRs and git log):

- Portfolio backtests simulate sleeves independently; portfolio caps and cross-sleeve netting are not simulated.
- No in-app portfolio manager loop: the manager is an external agent driving `thytrader-portfolio`.
- The risk policy has no pre-trade min-liquidity / max-spread check (repeated cycle failures do fence entries via safety supervision, ADR 0115).
- Backtests do not model latency, venue rejections, partial fills, or queue position.
- Backtests do not apply the account risk policy, so the fleet entry clustering and BTC-beta exposure caps (ADR 0125) are paper/live-only.
- Open-book PnL excludes estimated future exit fees (ADR 0100).
- Alerts do not fire on per-bar decision outcomes (`fleet-health` flags systemic ones, ADR 0130); there is no ThyTrader WebSocket (the UI polls).
- Only Coinbase spot is tradable live. Coinbase CFM futures have read-only surfaces (catalog,
  funding history, candles, account mirror; ADRs 0126 and 0127), backtests (ADR 0128) and paper
  books (perp-style only; ADR 0129); no live futures order path exists, and paper books do not
  run a dated-contract expiry flatten. Other exchanges are deferred.

## Required workflow

### 1. Establish repository state

- Run `git status --short --branch` before relying on branch or workspace assumptions.
- Read the relevant ADRs, `docs/architecture/` notes, and lane skill before changing behavior.
- Inspect manifests and neighboring code before assuming dependencies, symbols, or conventions.
- Never read or print real `.env` files or credential values.

### 2. Use GitNexus first

GitNexus is a first-class engineering tool for this repository. If it is not
available in this environment — missing MCP tools, missing `.gitnexus/run.cjs`
(`Cannot find module`), no usable index, or `gitnexus` not on PATH — **bootstrap
it from the repository root before editing**. Do not skip impact analysis or
`detect_changes` because the graph was absent at session start. Cloud agents,
fresh clones, and `git clean` checkouts must install the runner the same way.

Once `.gitnexus/run.cjs` exists:

```bash
node .gitnexus/run.cjs analyze --embeddings --pdg --index-only
```

If that file is missing, bootstrap (then retry the `node .gitnexus/run.cjs`
commands). Prefer `bunx` on npm 11, where `npx gitnexus` can crash
(`node.target is null`; [GitNexus #1939](https://github.com/abhigyanpatwari/GitNexus/issues/1939)):

```bash
bunx gitnexus@latest analyze --embeddings --pdg --index-only
```

`npx gitnexus analyze --embeddings --pdg --index-only` and
`pnpm --allow-build=@ladybugdb/core --allow-build=gitnexus --allow-build=tree-sitter dlx gitnexus@latest analyze --embeddings --pdg --index-only`
are equivalents. Details: [`.claude/skills/gitnexus-cli/SKILL.md`](.claude/skills/gitnexus-cli/SKILL.md).
Do not inject this duty into `ops/` instruction files.

1. Read `gitnexus://repo/thytrader/context` and check freshness.
2. Re-index stale data with the `analyze` command above (bootstrap first if the runner is missing).
3. Use GitNexus query/context/process tools for traversal before broad text search.
4. Use upstream impact analysis before nontrivial symbol/API changes.
5. Read source files to verify implementation details; the graph does not replace source inspection.
6. After changes, run GitNexus `detect_changes` and relevant route/shape/structural checks.
7. Re-index with embeddings and PDG after meaningful code changes so later agents do not use a stale graph.

If GitNexus and source disagree, source plus executed tests are authoritative; repair/re-index the graph.

### 3. Make narrow, tested changes

- Trace definitions and usages before editing.
- Keep FastAPI handlers thin; put business behavior in application/domain services.
- Keep Coinbase models inside the exchange adapter boundary.
- Depend on provider-neutral exchange, market-data, and broker contracts.
- Avoid premature microservices, speculative abstractions, and drive-by refactors.
- After meaningful verified work, commit and push to the current branch by default unless the
  user says not to. Do not rebase or rewrite history unless the user explicitly asks.

### 3a. Keep modules cohesive (no god files)

Every module holds one concern, and every package sits in one architectural layer.

- **Layering.** `tests/package_layers.json` declares the layers, highest first:
  - processes, then interfaces (agent CLIs and their loopback clients), then services;
  - adapters (PostgreSQL in `persistence`, the Coinbase broker), coordination (alerts, fleet
    control, portfolios), research, execution (the engine that places orders), memory,
    simulation (`backtest`), risk;
  - trading (the broker-neutral deployment, order, fill, ledger, exposure and sizing model and
    the store contract), evaluation (the deterministic run spec, indicators and signal
    evaluation every mode shares), contracts (`strategies`, `exchanges`), platform, market,
    foundation.

  A module may import its own layer or lower ones, and there are no upward imports today.
  CI (`tests/test_package_layers.py`) fails on any upward import not recorded in
  `allowed_upward_imports`, which is empty: never add an entry. It also fails on same-layer
  cycles. Fix an upward import by moving the code to the layer that owns it or by inverting
  the dependency: the lower package owns a protocol, and a process wires in the concrete
  store or adapter. Domain packages never import `persistence`. Place a new top-level
  package in a layer before using it. `uv run python -m tests.test_package_layers` prints
  any upward imports.
- **Size budget.** Python modules should stay near 600 lines and TS/Svelte near 400. CI enforces
  hard budgets of 800 and 600 (`tests/test_module_size_budget.py`). Every file is within budget
  and `tests/module_size_allowlist.json` is empty. Never add an entry: split the module.
- **Check concern spread before adding code to a file.** A file whose symbols belong to many
  communities mixes concerns:

  ```bash
  node .gitnexus/run.cjs cypher "MATCH (s)-[r:CodeRelation {type:'MEMBER_OF'}]->(c:Community) WHERE s.filePath = 'src/thytrader/<path>.py' RETURN c.id AS id, c.label AS area, c.cohesion AS cohesion, count(s) AS symbols ORDER BY symbols DESC" --repo .
  ```

  Put new code in the module that owns the community it joins, or a new module, never the
  nearest large file. Facades and barrels that only delegate or re-export are exempt.
- **Read GitNexus cohesion per community, not as a mean.** Cohesion is the share of a
  community's call edges that stay inside it. On this repo's size GitNexus clusters at high
  resolution, so cohesive files split into many 3–5 symbol fragments, and the repo-wide mean
  sits near 0.65 even for well-factored code; a mean near 1.0 is neither reachable nor a
  goal. Do not inline helpers or duplicate utilities to move it. Act on production
  communities of 10+ symbols under 0.8 that span several packages: they mark a concept with
  no home. Keep touched communities at or above 0.8. Find them with:

  ```bash
  node .gitnexus/run.cjs cypher "MATCH (c:Community) WHERE c.cohesion < 0.8 AND c.symbolCount >= 10 RETURN c.id, c.label, c.cohesion, c.symbolCount ORDER BY c.cohesion" --repo .
  ```

- **Split move-only.** Splitting a module is its own PR with no logic change.
  - Verify every moved definition is AST-identical, and that OpenAPI, the operator schema,
    CLI help and fingerprints are byte-identical.
  - Retarget importers to the new module. Keep a facade or barrel with an explicit `__all__`
    only where the old module is a deliberate public surface, such as `strategies.models`,
    `research.studies`, `market_data.datasets` or `web/src/lib/backtests.ts`.
  - Keep imports one-way with no new cycles, and keep logger names unchanged: a moved
    function keeps its logger by passing the original module name as a string to
    `logging.getLogger`.
  - When one class alone exceeds the budget, extract verbatim helpers or statement builders
    and prove they are equivalent (for example, by compiling the SQL); never change a
    statement.
  - A monkeypatch only applies in the module that looks a name up. Retarget string patches,
    using `tests/worker_patching.py` and `tests/loop_patching.py` (add new worker or loop
    modules to them) for the worker and loop, and prove the patches still fire.
  - Web splits must render an identical DOM across the e2e states they touch.
  - Re-index GitNexus after the split.
- **Delete dead code** in the same change that makes it dead. Confirm zero references with
  GitNexus and a whole-repo text search; an empty caller set alone is not proof.

### 4. Verify before reporting completion

Run the most targeted tests first, then the full checks. `.github/workflows/ci.yml` runs these on
every pull request and on `main`:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
cd web && npm run lint && npm run check && npm run test && npm run build
```

Run `npx playwright install chromium` in `web/` once on a new machine.

PostgreSQL-backed suites run in CI against a `postgres:17` service with the disposable database
`thytrader_ci_test`; locally they skip unless configured. To run them, migrate a separate loopback
test database whose name contains `test` (port `5439`, the application database, is refused) and
set both `THYTRADER_TEST_DATABASE_URL` and `THYTRADER_INTEGRATION_DATABASE_URL` to it. Never point
test variables at the running application's database, and leave `THYTRADER_DATABASE_URL` unset for
pytest so unconfigured-storage tests stay truthful.

When adding a migration, update `ops_contract.EXPECTED_SCHEMA_REVISION` in the same change
(`test_expected_schema_revision_matches_alembic_head` checks it against the Alembic head).
CI also runs `uv run alembic check` on the migrated database: the SQLAlchemy tables in
`src/thytrader/persistence/tables/` must match what the migrations create, column and table
comments included. Keep explanatory notes that no migration wrote as Python `#` comments. After a
rebuild (`make run`), verify operator health as well as the tests.

A change is complete only when relevant tests/checks pass, GitNexus impact is
reviewed, the ops-skills completion gate below is satisfied when it applies, and
`git diff` contains no unrelated edits.

## Python typing and code quality

Python code must be strongly and explicitly typed. Types are part of ThyTrader's design and debugging surface, not optional editor hints.

- Add complete parameter and return annotations to every function and method, including tests, callbacks, and special methods.
- Avoid implicit `Any`. Use `Any` only at a genuinely dynamic external boundary, document why it is unavoidable, validate it immediately, and narrow it before passing data into domain code.
- Prefer precise domain types, enums, `Literal`, `Protocol`, typed dataclasses, and validated models over primitive or loosely shaped dictionaries.
- Do not use `dict[str, object]` or stringly typed state as a substitute for a named domain model.
- Keep type casts and checker suppressions rare, narrow, and accompanied by a reason. Never use them merely to silence a design error.
- Preserve generic type parameters instead of erasing them through untyped helpers or decorators.
- Validate untrusted runtime data at system boundaries; static annotations do not validate Coinbase, HTTP, database, configuration, or agent payloads.
- Use explicit return types and exhaustive branching so impossible or unhandled states are visible to the type checker.
- All Python modules, packages, classes, functions, and methods require concise Google-style docstrings describing behavior and non-obvious invariants—not restating syntax.
- Imports belong at module scope and are sorted by Ruff. Function-local imports require a documented technical reason and must not hide a circular dependency that should be removed.
- Keep functions focused and below the configured complexity limit. Extract named, typed domain operations instead of nesting conditionals.
- `uv run ty check`, `uv run ruff check .`, and `uv run ruff format --check .` must pass without new suppressions before completion.

## Trading-system invariants

- Backtest, paper, and live execution must consume the same snapshotted strategy semantics.
- A signal or discretionary action creates an order intent; it does not bypass risk checks to call Coinbase directly.
- Persist order intent before submission and use unique client order IDs.
- A network timeout is ambiguous, not proof of order failure. Reconcile before retrying.
- Resume after restart only after reconciling balances, open orders, fills, and local state.
- Pause when exchange or local state cannot be reconciled safely.
- Block new risk-increasing orders on stale data or unhealthy required connections.
- Prefer maker execution for normal entries/TP, but permit marketable emergency exits.
- Disarming and kill switches must define whether cancellations and risk-reducing exits continue.
  Fleet disarm blocks entries only; managed stop keeps protection; flatten is separate
  ([ADR 0117](docs/decisions/0117-truthful-inventory-and-fleet-controls.md)).
- Synthetic trailing stops require durable state, healthy market data, and continuous worker supervision.
- The risk policy gates entries, never protective exits. Only `ENTRY`-purpose orders consume the
  entry-rate cap. Live cannot start until an operator publishes a policy
  (`LIVE_REQUIRES_PUBLISHED_POLICY`); the compiled default is a wide paper-research envelope.
- Unknown balances, marks, baselines, or accounting deny new risk; they are never reported or
  summed as zero. USD, USDC, and USDT amounts are never added together.
- The USDC spot balance is CFM futures collateral (ADR 0127 §8). Live USD/USDC spot entries
  pause while manual futures are in use or their state is unknown, unless a declared reserve
  covers the margin (ADR 0129). The pool is linked by rules, never by summing currencies.
- Account risk capital (observed venue quote plus managed inventory) is separate from bot
  allocations and from pinned performance capital
  ([ADR 0106](docs/decisions/0106-account-risk-capital-and-live-startup-baselines.md),
  [ADR 0107](docs/decisions/0107-capital-normalized-live-performance.md)).
- Venue order-observation time comes only from a successful venue read; migrations, local writes,
  and restarts never manufacture one
  ([ADR 0119](docs/decisions/0119-venue-order-observation-provenance.md)).

## Numerical and time correctness

- Do not use binary floating point for exchange quantities, prices, balances, fees, or order validation. Use `Decimal` or exact integer units and quantize against product increments.
- Vectorized floating-point arrays may be used for indicators/backtests when error characteristics are understood, but convert through explicit domain boundaries before execution.
- Use timezone-aware UTC internally.
- Define candle boundaries and event ordering explicitly.
- Prevent lookahead bias and report all fill/latency assumptions.
- Make backtests reproducible with strategy, dataset, engine, and seed fingerprints.

## Security and privacy

- Coinbase keys stay server-side. View + Trade is sufficient, but operator-selected keys with
  additional permissions must be accepted.
- Report detected permissions without treating extra permissions as implicit consent for actions.
- Never expose secrets through browser payloads, logs, exceptions, fixtures, support bundles, agent tools, or Git.
- The operator-chat LLM key lives in the API process only; it is not a Coinbase credential and is
  never echoed or logged.
- `.env.example` contains names/placeholders only; `.env` must remain ignored.
- Bind to loopback by default. Do not weaken startup safety to make remote access convenient.
  Public exposure would require TLS, authentication, secure sessions, CSRF protection, rate
  limiting, and a dedicated threat-model review; it is never enabled automatically.
- Agent observation stays read-only. Mutations use separate confirmation-gated tools
  (`--confirm`; live also `--i-understand-live`). Do not collapse skill lanes or weaken live-arming
  for agent convenience ([ADR 0030](docs/decisions/0030-agent-e2e-primary-surface.md)).

## Persistence and data

- PostgreSQL is authoritative for operational state and coordination.
- Parquet is for historical/analytical datasets; document partitioning and schema evolution.
- DuckDB and in-memory dataframes are not operational sources of truth.
- Migrations must be forward-safe, tested, and compatible with continuously running workers where applicable.
- Audit events must be useful, append-oriented, and redacted.

## Ops skills and operator docs (completion gate)

This gate applies to **contributors** changing ThyTrader. It does **not** apply in
the `ops/` workspace. Operating agents must never update documentation or source.

When a change touches an operator-facing surface (product behavior, CLI, HTTP agent
APIs, strategy semantics, timeframes, runtime, research, data ingest, or operator
reports), the **same change** must update:

1. The relevant `skills/thytrader-*` SKILL.md and its `references/` (canonical text
   operator agents read). `ops/.cursor/skills/` are symlinks into `skills/`; do not
   maintain a second skill tree.
2. The operator report schema when payloads change: regenerate
   `skills/thytrader-operator/references/operator-report-v1.schema.json` with
   `uv run python scripts/export_operator_schema.py` (never hand-edit it) and keep
   `uv run thytrader-operator schema-check` and the contract tests passing.
3. CLI `--help` when flags or invocation change.

Update `docs/user/` only when the browser UI changes. Record significant decisions
as a new ADR in `docs/decisions/` (and add its index row). There is no roadmap,
plans directory, or separate agent-integration doc; do not create them.

A slice is **not done** if ops skills would leave an operator agent unable to
discover or correctly invoke the new surface. Do not merge or report the work
complete until that agent can drive the surface from `skills/` alone, without
scraping logs or inventing commands.

## Operating a running instance

When the user asks to diagnose ThyTrader, inspect paper/live status, change strategies, run
backtests, or deploy/pause/resume/stop paper or live, **open the [`ops/`](ops/README.md)
workspace** and use the shipped skills ([`skills/README.md`](skills/README.md) lists the seven
lanes: operator, data, research, runtime, portfolio, playbook, memory) instead of scraping logs,
querying PostgreSQL, or editing source. Do not edit `src/`, `compose.yaml`, Dockerfiles, Alembic,
or tests while operating. Run `make run` only if the user asked to rebuild or restart, or if
health/HTTP reports a stale Compose image. Run every `uv run thytrader-*` command from this
repository root (the parent of `ops/`).

Lane boundaries are product invariants: operator and research never deploy, arm live, or cancel
orders; data ingest stays in the data lane; runtime control is not folded into operator, data, or
research; the playbook never inherits live authority; portfolio never deploys or places orders;
memory mutations never inherit YOLO.

Contributors update `skills/` in this checkout. Never teach the `ops/` workspace to edit
documentation or code; `ops/` stays operator-only.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **thytrader** (45104 symbols, 103104 relationships, 587 execution flows).

> Index stale? Run `node .gitnexus/run.cjs analyze --index-only` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? Bootstrap with `npx`, `bunx`, or `pnpm dlx` — e.g. `bunx gitnexus@latest analyze` (npm 11 npx crash; #1939).

## Always Do

- **MUST run impact before editing.** Use `impact({target: "symbolName", direction: "upstream"})` or `node .gitnexus/run.cjs impact "symbolName" --direction upstream --repo .`; report callers, processes, and risk. Never substitute grep for graph analysis. For unified PDG impact, add `mode: "pdg"` with optional `line: <N>` — it returns statement-level `affectedStatements` over CDG + REACHING_DEF and inter-procedural symbols in `interproceduralByDepth`/`byDepth`; no-layer/degraded PDG results are UNKNOWN-risk notes (`--pdg` layer). CLI equivalent: `node .gitnexus/run.cjs impact "symbolName" --direction upstream --mode pdg --line <N> --repo .`.
- **MUST analyze graph changes before committing.** Use `detect_changes({scope: "all"})` (MCP) or `node .gitnexus/run.cjs detect-changes --scope all --repo .` (CLI fallback). `partial: true` or `truncated: true` is not a clean check — a zero means unseen, not unaffected; re-run it. For regression review: `detect_changes({scope: "compare", base_ref: "main"})` or `node .gitnexus/run.cjs detect-changes --scope compare --base-ref "main" --repo .`.
- MUST warn on HIGH/CRITICAL `risk` pre-edit; never use `riskSharedAxes` to waive a HIGH/CRITICAL `risk` warning. Compare File/symbol: MCP File omits axes; Graph-RAG expands File.
- **MUST treat `risk: UNKNOWN` as unresolved, not as low.** An empty caller set is not evidence the symbol is unused — it can also mean the callers are not resolvable by the index (plain-object property access, dynamic dispatch, cross-language calls). `impact` pairs `UNKNOWN` with a `riskNote` saying so. Confirm with a text search before treating the symbol as safe to change or delete; do not proceed on the strength of a zero.
- **MUST use `query({search_query: "concept"})` for concepts/flows, `context({name: "symbolName"})` for a named symbol, or `impact` for blast radius, on read-only callers, dependencies, imports, or execution flow.** Graph first; text search only for empty/`UNKNOWN`/literals.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).
- For control/data dependence, `pdg_query({mode: "controls", target: "fileOrSymbol"})` answers "under what condition does X run?" (CDG, incl. guard clauses) and `pdg_query({mode: "flows", target, variable})` traces "where does variable Y flow?" (REACHING_DEF). `--pdg` layer.

## Never Do

- NEVER edit a function, class, or method before MCP/CLI impact analysis.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis, and never read `UNKNOWN` as an all-clear — it means the walk could not answer, which is the one verdict that requires confirming by other means.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit before MCP/CLI graph change analysis.

## Resources

| Resource | Use for |
| --- | --- |
| `gitnexus://repo/thytrader/context` | Codebase overview, check index freshness |
| `gitnexus://repo/thytrader/clusters` | All functional areas |
| `gitnexus://repo/thytrader/processes` | All execution flows |
| `gitnexus://repo/thytrader/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
| --- | --- |
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
