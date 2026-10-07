# Product Vision

ThyTrader is an open-source, local-first **Coinbase-first research and trading platform** that a
user or an authorized agent controls on a device they own: portfolio visibility, on-demand trades
with SL/TP, declarative strategies on every Coinbase candle clock, reproducible backtests and
studies, composed portfolios, and paper or explicitly armed live Coinbase **spot** execution, with
journals that record why each trade was made.

The **agent surface is the primary product** ([ADR 0030](../decisions/0030-agent-e2e-primary-surface.md)):
an agent can drive the whole loop through confirmation-gated skills and versioned HTTP. The
SvelteKit workstation stays a required, capable surface, and nothing requires an agent. Humans,
agents, or both together are all supported operating models
([ADR 0031](../decisions/0031-coinbase-first-platform-end-state.md)).

## Principles

1. **Safety before fee optimization.** Prefer maker execution, never at the cost of emergency
   risk reduction.
2. **One strategy, every runtime.** Backtest, paper, and live consume the same automatically
   snapshotted strategy rules and the same risk policy.
3. **Explicit assumptions.** Backtests disclose fill, fee, spread, and slippage models. Missing
   candles are never interpolated; unknown accounting is never reported as zero.
4. **Local-first security.** Secrets stay server-side, services bind to loopback, and network
   exposure is opt-in.
5. **Modular monolith.** Clear domain boundaries, separate supervised processes, no premature
   microservices.
6. **Observable and auditable.** Health, decisions, orders, fills, and performance are readable
   without raw storage or secrets.
7. **Agent-operable first.** A capability is incomplete until an agent can discover and invoke it
   from `skills/` alone.
8. **Gated authority.** Mutations need `--confirm`; live also needs `--i-understand-live`. Agents
   never receive unrestricted live control.

## Non-goals

- Hosted multi-tenant SaaS or public internet exposure by default.
- Derivatives, margin, or leverage.
- Additional exchanges until the Coinbase spot path is fully trustworthy.
- High-frequency trading claims or order-book queue simulation.
- Mobile-first UX or a visual node-canvas strategy editor.
- Weakening live-trading safety for agent or UI convenience.
