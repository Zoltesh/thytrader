# 0075: Selectable spot quote currencies including USDT

- Status: Accepted
- Date: 2026-09-19
- Supersedes: [0071](0071-usdc-spot-quote-markets.md) for the closed USD/USDC-only quote set

## Context

ADR 0071 opened USDC spot markets but kept `SpotQuoteCurrency = Literal["USD", "USDC"]`
and rejected other suffixes. Operator evidence on 19 Sep 2026 showed:

- Coinbase lists `BTC-USDC` / `ETH-USDC` as alias twins of USD books; ThyTrader dropped them.
- Portfolio HTTP labeled USDC cash as USD.
- The owner wants an explicit quote choice (USDT and other Coinbase spot quotes later) with
  an installation default of USDC for this operator.

## Decision

- Supported spot quotes are `USD`, `USDC`, and `USDT` through one shared
  `SPOT_PRODUCT_ID_PATTERN` and `SpotQuoteCurrency`.
- Installation default quote is `USDC` (`DEFAULT_SPOT_QUOTE_CURRENCY`). Create-draft
  defaults to `BTC-USDC`. Compiled risk-policy `quote_currency` defaults to `USDC`.
- Product identity is `product_id` plus `quote_currency_id`. Coinbase `alias_to`
  ids that match the spot pattern are expanded into distinct catalog rows; never
  key on `quote_display_symbol`.
- Per-action quote still comes from the chosen product id. Do not convert or proxy quotes.
- Multi-instrument documents still require one shared quote currency.
- Ops contract `thytrader-ops-contract-v34` advertises `spot_quote_currencies: ["USD", "USDC", "USDT"]`.

## Consequences

- Operators can select `BTC-USDC` even when Coinbase only lists it as an alias of `BTC-USD`.
- USDT products such as `BTC-USDT` are legal when Coinbase lists them.
- Portfolio valuations report `USDC` as the named currency at par for USD/USDC/USDT stables.
- Further quotes (EUR, etc.) still need an explicit ADR.

## Alternatives considered

- Keep USD/USDC only: rejected; the owner needs USDT and a default-USDC workstation.
- Accept every Coinbase quote suffix: rejected; fail-closed allow-list stays explicit.
