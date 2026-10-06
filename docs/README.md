# ThyTrader user guide

<p align="center">
  <img src="assets/thytrader-mark.svg" alt="ThyTrader mark" width="48">
</p>

<p align="center"><strong>thy</strong> trader &nbsp;=&nbsp; <strong>YOUR</strong> trader</p>

This is **your** workstation: research markets, write strategies, backtest them, paper-trade them,
and — only when you arm it — automate Coinbase **spot** live. It runs on a device you control.
An agent you authorize can drive that loop **100%**. You can also click every step yourself.

No roadmap. No gap-plan. No phase dump. Those stay with contributors.

## Start here

- [Product vision](product/vision.md) — the destination, in product language
- [Setup](user/setup.md) — `make run`, `make down`, loopback ports, Compose, native processes
- [Safety](user/safety.md) — secrets, loopback, confirmation, live arming
- [Operate](user/operate.md) — browser workspace (rail, ⌘K palette, Agent panel, themes), each
  bot's per-bar Decisions timeline, indicator operand offsets, open-book prices and PnL after paid entry fees,
  operator chat, and agent how-to
- [Agent portfolio + research playbook](agent/portfolio-research-ops-playbook.md) — numbered ops recipe for authorized agents

Skills of record: [`skills/README.md`](../skills/README.md). Open [`ops/`](../ops/README.md) when
you are operating a **running** instance (not changing source).

After updating, rebuild with `make run` and verify operator health; the database revision must
match the shipped migration head. See [Operate](user/operate.md) for persistent mismatch handling.

Bots with higher-timeframe filters load warmup for both current and previous completed bars,
including their first clock rollover. Missing coverage still pauses execution; see
[Operate](user/operate.md) for diagnosis and explicit resume after repair.

Lifecycle supervision remains per-product while paused, between bars, or warming history.
Canceled executions awaiting fills and applied-but-unprojected inventory are not flatness;
protection and committed live exits remain supervised across restart. See
[Operate](user/operate.md) before treating an empty position row as a successful flatten.

The Decisions timeline distinguishes protective-order maintenance from canceled entries and
explains exposure rejections with the actual capital and limit. See [Operate](user/operate.md).
Agents can read its complete recorded conditions and UTC signal evidence with
`thytrader-operator decisions --deployment-id UUID`; a diagnostic failure is distinct from an
empty timeline.
The operator strategy report includes the newest 100 library rows; follow its truncation warning
and use the research CLI to read older strategies or page the full library.

Home → Data health and `thytrader-operator data-health` show freshness across all enabled
watched markets, aligned to each candle clock. Published-tail health is separate from
historical coverage and a bot's own decision/reconciliation status.

Protection badges distinguish fresh live **order state** from independently audited venue stop
geometry. Only actual venue receipt timestamps supply freshness; local writes and legacy rows
cannot. Fresh order-state cover remains amber with its submitted-geometry limitation; paper says
**Worker stop**. See [protection evidence](user/operate.md) for timestamps and partial coverage.

Exchange diagnostics identify which account read failed and its safe error category.
Reconciliation links audit failures to their timestamps and observed recovery events.
See [account and reconciliation diagnostics](user/operate.md#account-and-reconciliation-diagnostics).

Strategy summaries show each indicator read's combined declaration and operand lag as
`(N bars ago)`. See [strategy authoring](user/operate.md#strategy-workspace).

Live return and drawdown use a pinned performance budget, including losses before the first
profit. Bot detail shows this budget separately from ledger cash; observed maximum drawdown
survives recovery and restart. See [performance accounting](user/operate.md#live-performance-accounting).

## What it is (and isn't)

**Isn't:** another hosted trading UI you rent.

**Is:** local-first, Coinbase-first research and trading. You keep the keys server-side. Missing
prices are never interpolated (an interval without trades is a flat no-trade bar). Live stays off until you arm it.

Group strategies into **portfolios** — sleeves with their own capital weights, a cash reserve, and
shared limits — backtest them together, then start the portfolio as one bot per sleeve under its
shared caps and loss stops. A manager agent can propose rebalances and pauses with its reasons;
you approve or decline anything outside its permissions, and it never places orders. Agents can
[create a complete portfolio with sleeves](user/operate.md#create-a-portfolio-from-a-file) in one
confirmed call; creation saves the definition and does not start trading.

You can:

1. **Go 100% human** — browser and CLIs, same confirmation gates any actor faces.
2. **Go 100% agent** — diagnosis, ingest, research, journals, notify, paper/live control through
   shipped skills, only with authority you grant.
3. **Share the wheel** — the agent edits a strategy; you backtest and arm (or the reverse).

Safety that outlives any one screen: [Safety](user/safety.md) and the
[security and trading-risk baseline](security-and-risk.md).


To select intended paper/live comparison partners, use **Paper/live twin** on Bot detail or
`thytrader-runtime link-twin BOT_ID --counterpart-deployment-id OTHER_BOT_ID --confirm`.
See [operating guide](user/operate.md) and the [runtime skill](../skills/thytrader-runtime/SKILL.md).

Async studies pin inputs before queueing and plan in workers ([ADR 0103](decisions/0103-worker-planned-async-studies.md)).
The library shows Mine / Research / All counts. A settling newest decision candle waits at most
120 seconds without new entries ([ADR 0104](decisions/0104-bounded-newest-candle-wait.md)).

Explicit twins may be strategy clones with server-verified identical pinned trading rules
(ADR 0105). Each comparison side names its actual snapshot fingerprint; pairing changes only
comparison metadata, never bot lifecycle or trading rules.

Manager briefings include the same journal-based open-book marks and paid-entry-fee PnL as bot
detail. Entry exposure and capital reservations count working entries, excluding verified exits.

Research reliability: [frozen campaigns, prospective validation, economic preflight,
stress assumptions, and bounded exports](user/research.md) are shipped through the research
HTTP/CLI lane and `/research` UI. They confer no deployment or order authority.

Backtest results also show [closed-trade fee attribution](user/research.md#closed-trade-fee-attribution):
PnL before trading fees, entry fees, exit fees, and net PnL, with accounting differences
disclosed. Agent result reports and exports carry the same evidence.
On narrow screens, result tables scroll inside their panels while exact reconciliation
and ratio values wrap. See [research results](user/research.md#closed-trade-fee-attribution).
