# 0091: Portfolio deployment, portfolio limits in the risk gate, and manager proposals

- Status: Accepted
- Date: 2026-10-02
- Relates to: [0088](0088-portfolio-model-and-portfolio-backtest.md) (portfolio model; this ADR
  ships its deferred follow-up), [0033](0033-phase-10-risk-policy-registry.md) (risk-policy
  registry and allocations), [0050](0050-daily-loss-drawdown-rate-collars.md) (per-book
  breakers), [0058](0058-protection-lifecycle-accounting.md) (pause, managed stop, flatten),
  [0064](0064-deployment-http-lifecycle-and-breaker-latch-reset.md) (lifecycle HTTP and latch
  reset), [0078](0078-live-readiness-http-ack-venue-reload-definite-rejects.md)
  (`i_understand_live`), [0087](0087-per-bar-decision-timeline.md) (decision timeline),
  [0030](0030-agent-e2e-primary-surface.md) (agent lanes)

## Context

ADR 0088 let operators compose portfolios, keep their journal, and backtest them together, but
nothing traded as a portfolio and the manager settings were inert. The product rules are fixed: a
portfolio is paper or live, never mixed; the manager agent moves capital and pauses sleeves while
strategies place every trade, and the manager never places orders; risk-policy allocations gate
LIVE membership only, and absolute quote caps are live-only.

## Decision

### Deploying a portfolio

Starting a portfolio starts **one deployment (bot) per sleeve**, created through the ordinary
deployment path and tagged with `deployments.portfolio_id` (Alembic 0056: nullable, set once at
creation, never rewritten by runtime saves, `ON DELETE SET NULL`). A paper sleeve starts with
`weight × capital_quote` of paper cash; a live sleeve's `allocated_capital` is
`weight × capital_quote`. A sleeve already running or paused for this portfolio is **attached**
(starting again is idempotent). Start names the portfolio `revision` the operator reviewed (409
`portfolio_revision_conflict` when stale) and is **planned for every target sleeve before anything
starts**: sleeve issues, a strategy busy as a standalone bot or in another portfolio (the existing
one-active-bot-per-strategy-and-mode rule stays), the strategy snapshot, the clock, and the risk
policy evaluated over every planned bot in turn. Any refusal is HTTP 422 `portfolio_start_rejected`
with `problems[]` and nothing starts. Live start and live resume need `i_understand_live` (HTTP 428)
and configured credentials. Pause, resume, and stop (managed stop by default, `flatten` on request)
apply the single-deployment semantics per sleeve, for the whole portfolio or one sleeve. A deployed
portfolio cannot be deleted (`portfolio_deployed`) and a deployed sleeve cannot be removed
(`portfolio_sleeve_deployed`); stopped bots outlive their portfolio row.

### Live allocation membership: approach (a)

**A live portfolio's sleeve allocations count as risk-policy allocation membership for its own
deployments.** When the published policy lists allocations (the live allowlist), a sleeve bot of a
live portfolio is admitted at start and at every entry as a member through its portfolio; its
reservation is its sleeve's `weight × capital_quote`, which is also its `allocated_capital`. If the
strategy is also listed in the policy, that allocation still bounds its exposure (strictest wins).
Standalone bots keep the existing semantics unchanged, and every other policy rule (published
policy for live, allowlist, running slots, paper book, account exposure caps, breakers, rates,
collars) applies to sleeve bots too.

### Portfolio limits in the risk gate

The execution worker loads every deployed portfolio's limits once per cycle and binds the
deployment's `PortfolioRiskBook` around its processing (a context scope, so the closed-bar loop
gains one argument at one call site). The entry gate applies, after the policy's membership checks
and before the account-wide exposure and breaker checks:

- `max_total_exposure_fraction × capital_quote` over every risk-bearing bot of the portfolio
  (position cost at entry price plus working entry remainders) → `PORTFOLIO_TOTAL_EXPOSURE_LIMIT`;
- `max_per_asset_fraction × capital_quote` per base asset, every product of the asset counted →
  `PORTFOLIO_ASSET_EXPOSURE_LIMIT`;
- a latched portfolio breaker → `PORTFOLIO_BREAKER_LATCHED`;
- a sleeve whose portfolio limits could not be read fails closed → `PORTFOLIO_LIMITS_UNAVAILABLE`.

Each verdict is a `RiskVerdict` with its reason code and a detail naming the amounts, so it lands in
the decision timeline (`bar_decisions`, ADR 0087) as `entry_blocked` with that code. The limits are
fractions of the portfolio's configured capital, not of its moving equity, so they do not loosen
after gains.

### Portfolio breakers

Every worker cycle, before any bot is processed, the worker supervises each portfolio with a running
or paused sleeve: it records the **run's equity** (capital plus the net PnL of every bot created
since the run started: persisted bar-close-marked equity minus starting equity), rolls the UTC day
open, and raises the run's high-water mark (`portfolio_runtime`, compare-and-set on its own
revision so a worker write can never undo an operator reset). `daily_loss_quote` trips when equity is
that far below the day's open (`PORTFOLIO_DAILY_LOSS_STOP`); `max_drawdown_fraction` trips at that
drawdown from the run's peak (`PORTFOLIO_DRAWDOWN_STOP`). A trip latches, pauses every running sleeve
with a `PORTFOLIO_*_STOP:` detail, and journals `breaker_tripped`. While latched, entries are denied,
start and resume are refused, and a sleeve resumed from its bot page is paused again next cycle.
Only an operator reset clears it; the reset re-baselines the day open and the peak at current
equity (otherwise the drawdown stop would trip again at once) and leaves sleeves paused until they
are resumed. A fresh start re-baselines at capital. The supervisor also keeps each sleeve bot's
`allocated_capital` equal to its current `weight × capital_quote`, so a rebalance binds new entries
on the next cycle; a paper sleeve sizes from the smaller of its paper cash and what is left of that
allocation (it never borrows cash it does not hold).

### The manager-loop contract

The LLM loop runs outside ThyTrader (Hermes or Claude through `skills/thytrader-portfolio`, later an
in-app agent). The contract is a proposals API plus a read-only briefing:

- **Proposals** (`portfolio_proposals`): `rebalance` (new weights for every sleeve, optional cash
  reserve), `pause_sleeve`, `resume_sleeve`, `add_sleeve` (strategy and weight), each with a
  rationale (1-2,000 characters), cited evidence (result, portfolio-backtest, or study
  fingerprints; decision refs `<deployment_id>/<product_id>@<bar_starts_at>`; deployment ids), and
  the revision it was planned against. There is no order kind; any other kind is refused with "the
  manager never places orders; strategies place every trade".
- **Auto-apply bounds.** Pausing a running sleeve applies at once with `may_pause_sleeves`. A
  rebalance applies at once only on a **paper** portfolio with `may_rebalance`, when the weight it
  moves (the larger of total increases and total decreases) fits what is left of the rolling 7-day
  `max_weight_change_per_week` budget of auto-applied rebalances. A live rebalance always waits:
  it moves real capital. Resume always waits (approving a live resume needs `i_understand_live`).
  Add-sleeve needs `may_propose_sleeves` to be proposed and always waits; approving adds the sleeve
  without starting it.
- **State machine.** `pending → applied | declined | failed | expired`, or `applied` at once when
  auto-applied. Approval re-plans the change against the portfolio as it is now (revision-guarded,
  in one transaction with the proposal row); a change that no longer fits is `failed` with its code.
  Unanswered proposals expire after 7 days; at most 20 wait at once.
- **Journal.** `proposal_submitted`, `proposal_approved`, `proposal_declined`, `proposal_failed`,
  `deployment_started|paused|resumed|stopped`, `breaker_tripped`, and `breaker_reset` join the
  journal kinds; the change itself is journaled with actor `manager` (agent and auto-applied
  changes) or `operator` (a person's decision), `reason: manager_proposal`, the proposal id, and the
  rationale.
- **Briefing** (`GET /api/v1/portfolios/{id}/briefing`, contract `thytrader-portfolio-briefing-v1`):
  mandate and permissions with the weekly budget used and left, deployment state, run performance,
  breaker and exposure versus caps, each sleeve with its bot, its newest portfolio-backtest evidence
  and `drawdown_vs_backtest`, recent per-bar decisions with citable refs, pending and recent
  proposals, the journal, and disclosures.

### Interfaces

HTTP under `/api/v1/portfolios/{id}`: `deployment`, `start`, `pause`, `resume`, `stop?flatten=`,
`sleeves/{sleeve_id}/start|pause|resume|stop`, `breaker/reset`, `proposals` (GET, POST),
`proposals/{proposal_id}` (GET), `proposals/{proposal_id}/approve|decline`, `briefing`. Portfolio
responses report `deployable` (sleeves present, no issues) and `deployment_state`; deployment
responses carry `portfolio_id`. CLIs: `thytrader-runtime portfolio-status|start|pause|resume|stop|reset-breaker`
(the runtime lane owns deployment; YOLO by the portfolio's mode tier like single bots; reset never)
and `thytrader-portfolio deployment|briefing|propose|proposals|show-proposal|approve|decline`
(`--confirm` always). Operator `portfolios` adds `deployment_state`, the breaker, and pending
proposals (`PORTFOLIO_BREAKER_LATCHED` degrades it). Ops contract `thytrader-ops-contract-v51` adds
`portfolio_deployment`, `portfolio_breakers`, `portfolio_proposal_kinds`, and
`portfolio_briefing_contract`, with expected Alembic `0056`. The Portfolio page gains Start /
Pause all / Resume / Stop (live start and resume behind the real-orders checkbox), each sleeve's
bot status and controls, Manager-tab proposals with Approve / Decline / Ask why (opens the Agent
panel with the proposal as context), and the breaker state with a reset on the Limits tab.

## Consequences

- Portfolios trade: sleeve bots run under the portfolio's shared caps and stops on top of their
  own checks and the account policy, and every refusal says which limit and by how much.
- An operator can let an agent manage a paper portfolio within a bounded weekly budget, while every
  live capital move, every resume, and every new sleeve waits for a person.
- Portfolio equity and its breakers lag by up to one bar of each sleeve's clock (persisted
  bar-close marks), the same staleness the per-bot breakers have.
- Rebalances do not resize open positions; a paper sleeve's raise only binds up to its paper cash
  until it restarts.
- Portfolio backtests still simulate sleeves independently (ADR 0088's disclosure stands).

## Alternatives considered

- **(b) Require matching policy allocations for live sleeves.** Rejected: two sources of truth for
  live capital that drift on every rebalance, and the manager could never keep them in sync because
  it has no runtime or policy authority. Approach (a) keeps one reservation (the sleeve) and leaves
  standalone semantics untouched.
- **Thread the portfolio book through every loop helper.** Rejected to keep the closed-bar loop and
  its signatures stable; a fail-closed context scope bound by the worker gives the gate the book.
- **Limits as fractions of current equity.** Rejected: caps that grow with gains loosen exactly when
  risk has accumulated; the configured capital is stable and explainable.
- **Auto-apply live rebalances inside the budget.** Rejected for now: moving real capital between
  strategies is a person's decision; the permission still auto-applies on paper, where the manager
  earns trust.
- **Resume sleeves on breaker reset.** Rejected: a reset acknowledges the loss; resuming is a
  separate, explicit decision (live needs the acknowledgement).
