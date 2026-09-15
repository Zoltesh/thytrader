# 0030: Agent-driven E2E as the primary product surface

- Status: Accepted
- Date: 2026-09-15
- Relates to: [0001](0001-sveltekit-frontend.md), [0012](0012-operator-diagnostics.md),
  [0013](0013-http-first-agent-clients.md), [0031](0031-coinbase-first-platform-end-state.md)
- Supersedes: the product framing that ThyTrader's three operating models are **equal** and that
  none is privileged as the design target (README, [product vision](../product/vision.md),
  [agent integration](../agent-integration.md)). It does **not** supersede ADR 0001 (SvelteKit
  remains the application UI), ADR 0012, or ADR 0013 (skill lanes and confirmation gates stay).

## Context

ThyTrader already ships four agent skills, versioned HTTP contracts, and a SvelteKit workstation UI.
Product copy treated 100% human-driven, 100% agent-driven, and collaborative use as **equal**
operating models, with "none privileged as the real way to use it."

That framing understates the product destination. The completeness bar is an authorized agent that
can drive research and trading end to end: learn patterns, keep journals, analyze sentiment,
research markets, build and backtest strategies, deploy paper and live, monitor, and notify. A
modern professional UI still matters, but it is not a substitute for agent-operable APIs and skills.

ADR 0001 chose SvelteKit because the **application UI** is a dashboard, not a content site. That
frontend decision is unchanged. ADR 0012 and 0013 chose HTTP-first, lane-separated, confirmation-gated
agent tools. Those safety boundaries are unchanged. What changes is product-surface primacy.

## Decision

- **Agent-driven end-to-end operation is the primary product surface.** New capabilities are
  incomplete until they have a supported, versioned, confirmation-gated agent contract (CLI + HTTP +
  skill), not merely a browser path.
- **A modern professional UI remains required.** SvelteKit/Svelte 5 (ADR 0001) stays the application
  frontend. Humans must be able to do the same observations and mutations the agent can, through the
  same confirmation and risk gates.
- **Three operating models remain fully supported;** none is deprecated, and nothing in the product
  *requires* an agent:
  1. 100% human-driven (browser and CLIs);
  2. 100% agent-driven within explicitly granted, confirmation-gated authority
     (`--confirm`; live also `--i-understand-live`);
  3. collaborative human + agent.
- Agent E2E is the **design target and completeness bar**, not a fourth privilege that bypasses
  risk, audit, or live-arming rules.
- Skill authority stays lane-separated: operator (read-only), data, research, and runtime do not
  inherit trading power from one another (ADR 0012, ADR 0013).
- Planned journals, sentiment, pattern learning, and notifications (roadmap Phase 14 and later)
  stay subordinate to confirmation gates, scoped authority, immutable evidence, and risk controls.
  They are not shipped by this ADR.

## Consequences

- Contributors and coding agents judge a feature done when the agent contract exists, is tested, and
  is documented in `skills/` — not when only the UI works.
- README and product vision stop calling the three operating models equal in primacy.
- Phase 12 orchestration / YOLO opt-in and Phase 14 experiential memory become more important as
  product work; they still do not weaken live-arming or collapse skill lanes.
- UI polish remains in scope; it must not outrun or replace agent-operable APIs.

## Alternatives considered

- **Keep three equal operating models with no privileged design target:** rejected; later agents
  would treat the UI as sufficient and leave skills incomplete.
- **UI-first, agents as optional helpers:** rejected; it contradicts the destination product and
  invites log-scraping shortcuts.
- **Agent-only (no capable UI):** rejected; humans must retain full operating capability, and ADR
  0001's dashboard UI remains a first-class surface.
- **Collapse operator/data/research/runtime into one unrestricted agent:** rejected; trading
  authority must not inherit from observation or research (ADR 0012, ADR 0013).
