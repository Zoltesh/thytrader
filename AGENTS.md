# AGENTS.md

## Mission

Build ThyTrader as a trustworthy, open-source, local-first trading workstation: portfolio visibility, declarative strategy design, reproducible backtesting, paper execution, and explicitly armed live Coinbase spot trading.

Financial correctness, restart safety, secret hygiene, and auditability outrank delivery speed. Read [the documentation index](docs/README.md) before changing architecture or product behavior.

## Current direction

- Backend: FastAPI and Python managed with `uv`.
- Frontend: SvelteKit/Svelte 5 with strict TypeScript.
- Runtime: modular monolith with separate API and continuously running worker processes.
- Initial exchange: Coinbase Advanced Trade REST v3 and WebSockets, spot only.
- Storage: PostgreSQL for operational state; Parquet with Polars/DuckDB for analytics.
- Deployment: Docker Compose for supported installs; native processes for development.
- Strategy model: immutable, versioned declarative schema shared by backtest, paper, and live runtimes.
- Access: loopback-only by default; remote exposure must be explicit and protected.

Accepted decisions live in `docs/decisions/`. Do not silently contradict an accepted ADR. Add a superseding ADR and update related docs when direction changes.

## Required workflow

### 1. Establish repository state

- Run `git status --short --branch` before relying on branch or workspace assumptions.
- Read the relevant product, architecture, security, and ADR documents.
- Inspect manifests and neighboring code before assuming dependencies, symbols, or conventions.
- Never read or print real `.env` files or credential values.

### 2. Use GitNexus first

GitNexus is a first-class engineering tool for this repository.

1. Read `gitnexus://repo/thytrader/context` and check freshness.
2. Re-index stale data with:

   ```bash
   node .gitnexus/run.cjs analyze --embeddings --pdg --index-only
   ```

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

### 4. Verify before reporting completion

Run the most targeted tests first, then the broader available checks. Use commands defined by the current manifests; do not claim checks that the repository does not yet provide.

Expected Python quality commands once their corresponding configuration/tests exist:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

For frontend work, run the package-manager scripts for formatting, linting, tests, and production build from the actual frontend manifest.

A change is complete only when relevant tests/checks pass, GitNexus impact is reviewed, documentation is updated where needed, and `git diff` contains no unrelated edits.

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

- Backtest, paper, and live execution must consume the same published strategy semantics.
- A signal creates an order intent; it does not bypass risk checks to call Coinbase directly.
- Persist order intent before submission and use unique client order IDs.
- A network timeout is ambiguous, not proof of order failure. Reconcile before retrying.
- Resume after restart only after reconciling balances, open orders, fills, and local state.
- Pause when exchange or local state cannot be reconciled safely.
- Block new risk-increasing orders on stale data or unhealthy required connections.
- Prefer maker execution for normal entries/TP, but permit marketable emergency exits.
- Disarming and kill switches must define whether cancellations and risk-reducing exits continue.
- Synthetic trailing stops require durable state, healthy market data, and continuous worker supervision.

## Numerical and time correctness

- Do not use binary floating point for exchange quantities, prices, balances, fees, or order validation. Use `Decimal` or exact integer units and quantize against product increments.
- Vectorized floating-point arrays may be used for indicators/backtests when error characteristics are understood, but convert through explicit domain boundaries before execution.
- Use timezone-aware UTC internally.
- Define candle boundaries and event ordering explicitly.
- Prevent lookahead bias and report all fill/latency assumptions.
- Make backtests reproducible with strategy, dataset, engine, and seed fingerprints.

## Security and privacy

- Coinbase keys stay server-side and require only View + Trade permissions.
- Transfer-enabled keys must be rejected or blocked during setup.
- Never expose secrets through browser payloads, logs, exceptions, fixtures, support bundles, agent tools, or Git.
- `.env.example` contains names/placeholders only; `.env` must remain ignored.
- Bind to loopback by default. Do not weaken startup safety to make remote access convenient.
- Agent/operator interfaces are read-only until separate mutation tools and confirmation policies are explicitly designed.

## Persistence and data

- PostgreSQL is authoritative for operational state and coordination.
- Parquet is for historical/analytical datasets; document partitioning and schema evolution.
- DuckDB and in-memory dataframes are not operational sources of truth.
- Migrations must be forward-safe, tested, and compatible with continuously running workers where applicable.
- Audit events must be useful, append-oriented, and redacted.

## Documentation rules

Update documentation in the same change when modifying:

- product scope or accepted architecture;
- strategy schema or runtime semantics;
- risk controls or execution policy;
- storage contracts or deployment behavior;
- public API/CLI or operator diagnostics;
- setup, quality, or GitNexus workflow.

Use an ADR for durable choices with meaningful alternatives. Supersede old ADRs rather than deleting their history.
