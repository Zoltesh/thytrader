---
name: thytrader-contributor-ops-docs
description: >-
  When implementing or finishing ThyTrader product, CLI, HTTP agent API, strategy,
  timeframe, runtime, research, data ingest, or operator-report changes as a
  contributor. Same-change completion gate for skills/, the operator schema, CLI help, and ADRs.
  Never use this skill in the ops/ workspace or while operating a running instance.
---

# ThyTrader contributor ops-docs gate

If you are in the `ops/` workspace or operating a running instance, stop. Do not
update documentation or code. Use the shipped `thytrader-*` operator skills.

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

Keep existing safety: `--confirm` on mutations; live also `--i-understand-live`;
skill lanes stay separate; no log scraping; no secrets.

Canonical copy also lives in root `AGENTS.md`.
