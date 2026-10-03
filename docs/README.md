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

The Decisions timeline distinguishes protective-order maintenance from canceled entries and
explains exposure rejections with the actual capital and limit. See [Operate](user/operate.md).

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
