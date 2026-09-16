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
- [Setup](user/setup.md) — `make run`, loopback ports, Compose, native processes
- [Safety](user/safety.md) — secrets, loopback, confirmation, live arming
- [Operate](user/operate.md) — browser workspace and agent how-to

Skills of record: [`skills/README.md`](../skills/README.md). Open [`ops/`](../ops/README.md) when
you are operating a **running** instance (not changing source).

## What it is (and isn't)

**Isn't:** another hosted trading UI you rent.

**Is:** local-first, Coinbase-first research and trading. You keep the keys server-side. Missing
candles are never interpolated. Live stays off until you arm it.

You can:

1. **Go 100% human** — browser and CLIs, same confirmation gates any actor faces.
2. **Go 100% agent** — diagnosis, ingest, research, journals, notify, paper/live control through
   shipped skills, only with authority you grant.
3. **Share the wheel** — the agent drafts; you publish and arm (or the reverse).

Safety that outlives any one screen: [Safety](user/safety.md) and the
[security and trading-risk baseline](security-and-risk.md).
