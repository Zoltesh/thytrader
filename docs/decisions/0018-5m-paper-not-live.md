# 0018: 5m paper on the same published clock, live stays 1h

- Status: Accepted
- Date: 2026-09-11
- Relates to: [0014](0014-watchlist-and-5m-research.md), [0016](0016-longer-complete-5m-datasets.md), [0017](0017-maker-limit-bar-backtest.md)

## Context

Agents were researching 5m strategies while `create_deployment` rejected anything but 1h, and the
execution worker snapped windows to UTC hours. Longer complete 5m datasets and maker-aware v3
backtests now exist. Paper still needs the same closed-bar clock as research. Live 5m still lacks
user-order WebSockets, native stops, and a daily-loss kill policy.

## Decision

Paper may start a published `5m` strategy and evaluate closed 5m bars. Live remains `1h` only.
`CandleInterval.execution_supported` includes 5m for paper consumption; `create_deployment` keeps
the live 1h guard. The worker loads `get_preview` / `get_range` for the strategy interval and steps
`new_closed_bars` by `interval.duration`. Missing latest bars still pause (`due is None`).

## Consequences

- A 5m paper deployment and a v3 backtest can share one published strategy fingerprint.
- Live 5m stays HTTP 409. Do not treat paper 5m as consent to arm live 5m.
- Same-bar stop comparison uses the fill bar's `starts_at`, not an hourly snap.

## Alternatives considered

- Enable 5m live with paper: rejected until microstructure (user-order WS, native stop/OCO,
  durable trailing, daily-loss kill) exists.
- Keep paper on 1h while research is 5m: rejected; that is the split-clock gap this series closes.
