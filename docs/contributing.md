# Contributor documentation

This is the **contributor** index: architecture, ADRs, roadmap, and in-repo plans. User-facing
pages start at [docs/README.md](README.md) (setup, safety, operate). Coding agents follow root
[`AGENTS.md`](../AGENTS.md).

Planning files stay **in git**. Do not gitignore `docs/roadmap.md`, `docs/plans/`, or
`docs/decisions/`.

Operating a running instance is a different workspace: open [`ops/`](../ops/README.md). Do not
teach `ops/` instruction files to edit documentation or source.

## Architecture

- [Architecture overview](architecture/overview.md)
- [Market-data pipeline](architecture/market-data.md)
- [Strategy and backtesting design](architecture/strategy-and-backtesting.md)
- [Canonical strategy schema](architecture/canonical-strategy-schema.md)
- [Immutable research-run specification](architecture/research-run-specification.md)
- [Deterministic signal evaluation](architecture/signal-evaluation.md)
- [Deterministic bar-level backtest simulation](architecture/backtest-simulation.md)
- [Research studies (walk-forward, OOS, cross-market, sweeps, WFO)](architecture/research-studies.md)

## Decisions, roadmap, plans

- [Architecture decision records](decisions/README.md) — includes
  [ADR 0044](decisions/0044-parameter-sweeps-wfo-stitched-equity.md) (parameter sweeps, walk-forward
  optimization, stitched OOS equity) and
  [ADR 0045](decisions/0045-spot-shorting-and-attached-entry-brackets.md) (spot shorting and attached
  entry brackets)
- [Delivery roadmap](roadmap.md)
- [Ops field report: 5m data → research → paper (2026-09-11)](plans/2026-09-11-ops-5m-research-paper-field-report.md) —
  **historical** running-instance evidence; stale Compose, 14-day clip, v3 422, and 5m paper 409 are
  closed by ADRs 0019, 0017/0018, and 0024
- [Agent-driven platform gap plan (2026-09-12)](plans/2026-09-12-agent-driven-platform-gap-plan.md)
- [Fee-tier suggested defaults (2026-09-13)](plans/2026-09-13-fee-tier-research-defaults.md)

## Also used by operators (not the user landing page)

- [Security and trading-risk baseline](security-and-risk.md)
- [Agent/operator integration](agent-integration.md)
- Canonical skills: [`skills/README.md`](../skills/README.md)

## Document roles

- **Product documents** describe who ThyTrader serves and what it should do.
- **User documents** (`docs/README.md`, `docs/user/`) describe setup, safety, and how to operate.
- **Architecture documents** describe system boundaries and durable technical direction.
- **Decision records** explain important choices, alternatives, and consequences.
- **Roadmap and plans** sequence work without pretending dates or scope are guaranteed.
- **`AGENTS.md`** gives coding agents repository-specific operating instructions, including the
  ops-skills completion gate. That gate does **not** belong in `ops/` instruction files.

## Updating documentation

When a meaningful decision changes:

1. Add or supersede an architecture decision record.
2. Update the affected architecture, product, security, or roadmap document.
3. Update `AGENTS.md` if the engineering workflow or quality gates changed.
4. If the change is a product surface operators invoke, satisfy the ops-skills completion gate in
   root `AGENTS.md` (canonical `skills/`, operator schemas, operator-facing `docs/`). Never copy
   that duty into `ops/AGENTS.md` or `ops/.cursor/rules/`.
5. Keep examples free of real credentials, account IDs, balances, and other sensitive data.
6. Include documentation validation in the same change as the implementation.

An accepted decision is not immutable. Supersede it explicitly so future contributors can
understand both the current direction and why it changed.

## Native quality gates

Frontend:

```bash
cd web
npx playwright install chromium  # first run on a new machine only
npm run lint
npm run check
npm run test
npm run build
```

Backend:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

Native process startup is documented for operators in [user setup](user/setup.md).
