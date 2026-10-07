# 0081: Global live chrome, Portfolio, bot detail, and Trade review

- Status: Accepted; superseded in part by [0083](0083-unified-backtest-model.md)
- Superseded part: the Test run bar engine default from `engine-support`. There is no engine picker; the run bar shows the "How backtests simulate" disclosure.
- Amended by [0087](0087-per-bar-decision-timeline.md): bot detail's "Why it traded" timeline is now the per-bar Decisions timeline (trade reasons merged into their bars), and the header shows "Next evaluation ≈".
- Date: 2026-09-30
- Relates to: [0079](0079-four-destination-shell-agent-panel-palette-tokens.md),
  [0080](0080-per-strategy-workspace-build-test-run-why.md),
  [0078](0078-live-readiness-http-ack-venue-reload-definite-rejects.md),
  [0065](0065-deployment-capital-accounting-http.md), [0054](0054-trade-reason-journals.md)
- Design spec: strategy-version workspace

## Context

After [ADR 0080](0080-per-strategy-workspace-build-test-run-why.md) the Portfolio destination
(`/deployments`), bot detail (`/deployments/[id]`), and Trade (`/trade`) were still the older card
and form layouts. Nothing in the shell told an operator that the page in front of them moves real
money: live and paper looked the same apart from a chip. Trade confirmed live orders with
`window.confirm`, and bot detail's live resume sent `i_understand_live: true` without the explicit
"real orders" checkbox the Run stage already required.

## Decision

- **Live chrome is declared by routes, rendered by the shell.** A route that shows live exposure
  or composes a live order calls `declareLiveContext({ kind, productId, cap })` from an `$effect`
  and returns its release function. The shell renders an amber strip under the top bar
  (`LIVE: this bot places real Coinbase orders · ETH / USDC · allocated 100.00 USDC`, warning icon,
  polite live region) and an inset frame around the main column. Last declaration wins; releasing
  an older one never clears a newer one. Declared by: live bot detail, Trade in Live mode, and the
  Run stage while the live-arm dialog is open. The text always says LIVE (never color alone); the
  strip's fade-in runs only without `prefers-reduced-motion: reduce`.
- **Portfolio** is the bounded offset-paged inventory in groups Needs attention (any status other
  than running / paused / stopped, unchanged) → Running → Paused → Stopped, with an All / Paper /
  Live filter. Header counts come from the full inventory; allocated capital, performance equity,
  and gross marked exposure are summed exactly per quote currency and never across paper and live.
  A figure that cannot be computed (no capital block, an open book without a complete mark, a
  multi-book ledger mark) is `—` with the reason. Names and versions resolve from the strategy
  library by exact fingerprint, else the short fingerprint. Multi-strategy portfolios (sleeves,
  portfolio backtest, manager agent) are not built; the page says so once.
- **Bot detail** keeps every existing contract rule (lifecycle contract gating, unknown
  `lifecycle_command` read-only, managed stop vs `?flatten=true`, no Resume on stopped,
  outcome-unknown and stale-refresh blocking) and recomposes the page: header with mode chip,
  exact-fingerprint version pill, market · clock · status · lease, and controls; four KPI cards
  (capital block, operator performance report with ledger fallback, position and protection,
  latest completed-bar signal); one Orders & fills card with an Orders / Fills switch over the
  existing cursor paging; a "Why it traded" timeline shared with the Why stage; then evidence,
  sibling deployments, and capital / configuration disclosures. Live resume requires the "I
  understand this places real orders" checkbox before `i_understand_live: true`;
  `DeploymentLifecycleDialog` now reports that acknowledgement to its caller and is built on the
  shared native `ConfirmDialog`.
- **Trade** keeps every field, validation, and safety sentence and adds a Review aside (entry, max
  loss at stop, reward : risk from exact rational arithmetic; **Unknown** for marketable entries;
  risk-policy publication read from `GET /api/v1/risk-policy`). Live submit uses a `ConfirmDialog`
  whose confirm is disabled until the real-orders checkbox is ticked. `window.confirm` is gone.
- **Test stage run bar** is one compact row; slippage, spread stress, and custom dates move behind
  an Advanced disclosure whose summary still shows their values. The engine defaults to the newest
  engine that `GET /api/v1/research/engine-support` advertises **and** the browser launcher offers
  and discloses (V3); an unreadable matrix keeps the explicit "Select an engine" choice.

No backend contract changes.

## Consequences

- Every browser live mutation (arm, resume, place-order) is gated by the same checkbox.
- Money on Portfolio is only ever a truthful per-mode, per-quote total or `—`.
- V4 stays a research-CLI/agent engine in the browser until its assumptions are disclosed on result
  detail; the launcher default follows the intersection, not the server's newest engine alone.

## Alternatives considered

- **Per-page live banners:** rejected; each page would re-implement the wording and frame, and a
  missed page would silently look like paper.
- **Totalling paper and live capital under All:** rejected; simulated and real money are not
  additive.
- **Defaulting to V4 without launcher support:** rejected; the result page cannot yet describe V4
  fills and costs, so a default would hide assumptions.
