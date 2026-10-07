# 0080: Per-strategy workspace (Build · Test · Run · Why)

- Status: Accepted; superseded in part by [0082](0082-strategy-root-mutable-strategies-auto-snapshots.md)
- Amended by [0087](0087-per-bar-decision-timeline.md): the Why stage shows the per-bar Decisions timeline across the strategy's deployments with a deployment selector.
- Superseded part: the `?version=` context, Draft/Published vN pills, version picker, Versions dialog, publish, revise, and archive. Build saves in place; Test/Run start from the current definition; rows show Current rules / Earlier edit; the library gains bulk delete.
- Date: 2026-09-29
- Relates to: [0079](0079-four-destination-shell-agent-panel-palette-tokens.md),
  [0054](0054-trade-reason-journals.md), [0053](0053-workstation-ia-write-only-coinbase-credentials.md),
  [0065](0065-deployment-capital-accounting-http.md), [0078](0078-live-readiness-http-ack-venue-reload-definite-rejects.md)
- Design spec: strategy-version workspace

## Context

[ADR 0079](0079-four-destination-shell-agent-panel-palette-tokens.md) folded Research, Backtests,
and Deploy under the Strategies destination but kept them as separate pages. An operator still had
to join one strategy version across four routes (library drawer, `/research`, `/backtests`,
`/deploy`) and the builder. The strategy-version workspace spec requires one exact version
identity carried through every stage, with no readiness verdict, no backtest→deployment promotion,
and fail-closed handling of an invalid requested version.

## Decision

Each strategy gets one workspace, a SvelteKit layout at `/strategies/[id]`:

| Stage | Route | Absorbs |
| --- | --- | --- |
| Build | `/strategies/[id]` | the builder; read-only published definition when there is no draft |
| Test | `/strategies/[id]/test` | `/research` launch and studies; this strategy's `/backtests` results with inline detail (`?result=`) |
| Run | `/strategies/[id]/run` | `/deploy` start form; Paper and Live cards; live preflight |
| Why | `/strategies/[id]/why` | latest completed-bar signal and trade reasons per deployment |

- **Version context.** `?version=<strategy_fingerprint>` selects the exact published version for
  every stage, defaulting to the latest published version. A fingerprint that is not one of the
  strategy's published versions renders an error and no stage content, so no mutation can run
  against a different version.
- **Identity bar.** Sticky name, Draft/Published vN, version picker (published versions plus the
  open draft), short fingerprint with copy, market label from the product's own quote
  (`BTC / USDC`) with the canonical product id disclosed, clock, draft state, and Versions / Clone.
  Stage navigation is plain links with `aria-current="page"`, not an ARIA tablist.
- **Library.** Rows open the workspace and show an evidence pipeline (Build / Test / Paper / Live)
  derived only from the library row payload. Archive confirms in an accessible dialog. The old
  hover toolbar and Insight/Versions drawer are removed; Versions moved into the workspace.
- **Test.** No Deploy or Start-paper action exists on backtest results.
- **Run.** Deployments are filtered by exact fingerprint; other versions are listed separately.
  Pause / resume / stop / flatten use `DeploymentLifecycleDialog` (managed stop is `POST /stop`,
  flatten is `POST /stop?flatten=true`). `window.confirm` is gone. Arming live and resuming live
  require an "I understand this places real orders" checkbox before `i_understand_live: true` is
  sent.
- **Live preflight** reads existing endpoints only: credential presence
  (`GET /api/v1/credentials/coinbase`), published risk policy and per-strategy allocation
  (`GET /api/v1/risk-policy`), quote-currency available balance (`GET /api/v1/portfolio`, Unknown
  for demo snapshots), and for sub-hour clocks the user-order feed state
  (`GET /api/v1/operator/runtime` → `payload.user_order_feed`). Each item is independent;
  unreadable sources show `Unknown`; nothing is combined into a readiness verdict and arming is not
  gated by the checklist. Paper evidence is informational only.
- **Why** reads `GET /api/v1/memory/trade-reasons?deployment_id=` per deployment of the selected
  version and states that full per-bar decision history is not recorded yet. This amends
  [ADR 0054](0054-trade-reason-journals.md)'s "Memory and Trade" surface list: the workspace Why
  stage is a third, version-scoped review surface for the same payload (not a new contract).
- **Redirects.** `/research?strategy=X[&strategy_fingerprint=F]` → Test and
  `/deploy?strategy=X[...]` → Run (server-safe 307 in `+page.ts`, fingerprint forwarded as
  `?version=` so it still fails closed). `/backtests?result=R` and
  `/backtests?strategy_fingerprint=F` open the owning workspace when the fingerprint is a published
  version of a strategy (source → history check); otherwise the standalone view stays. The three
  routes without parameters show a small chooser pointing at `/strategies` (`/backtests` keeps
  its recent-results list).

No backend contract changes.

## Consequences

- One exact version identity is visible on every stage; deep links from deployment detail point at
  the Run / Test / Why stages for that fingerprint.
- Agent and CLI surfaces are unchanged; skills describe the new browser routes only.
- Live preflight can show `attention` items while arming stays possible: the operator decides.

## Alternatives considered

- **Keep separate pages with shared identity headers:** rejected; still four joins per version.
- **A readiness score for live:** rejected; the spec forbids one backend readiness state and a
  client-side verdict would imply one.
- **Tabs (ARIA tablist) for stages:** rejected; stages are routes, and mixing links into a
  tablist breaks keyboard semantics.
