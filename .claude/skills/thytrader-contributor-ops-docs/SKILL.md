---
name: thytrader-contributor-ops-docs
description: >-
  When implementing or finishing ThyTrader product, CLI, HTTP agent API, strategy,
  timeframe, runtime, research, data ingest, or operator-report changes as a
  contributor. Same-change completion gate for skills/ and operator-facing docs.
  Never use this skill in the ops/ workspace or while operating a running instance.
---

# ThyTrader contributor ops-docs gate

If you are in the `ops/` workspace or operating a running instance, stop. Do not
update documentation or code. Use the shipped `thytrader-*` operator skills.

This gate applies to **contributors** changing ThyTrader. It does **not** apply in
the `ops/` workspace. Operating agents must never update documentation or source.

When a change touches any of: product surfaces, CLI, HTTP agent APIs, strategy
semantics, timeframes, runtime, research, data ingest, or operator reports, the
**same change** must update:

1. The relevant `skills/thytrader-*` SKILL.md (canonical skill text operator
   agents read). `ops/.cursor/skills/` are symlinks into `skills/`; do not
   maintain a second skill tree.
2. Operator report schemas if payloads changed
   (`skills/thytrader-operator/references/` and related contract tests).
3. `docs/` that operators and agents read (user-facing `docs/README.md` and
   `docs/user/`, plus `docs/agent-integration.md`, and roadmap shipped vs
   destination when slice status changes). Update CLI `--help` when flags or
   invocation change.

A slice is **not done** if ops skills would leave an operator agent unable to
discover or correctly invoke the new surface. Do not merge or report the work
complete until that agent can drive the surface from `skills/` alone, without
scraping logs or inventing commands.

Keep existing safety: `--confirm` on mutations; live also `--i-understand-live`;
skill lanes stay separate; no log scraping; no secrets.

Canonical copy also lives in root `AGENTS.md`.
