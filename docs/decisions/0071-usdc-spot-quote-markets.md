# 0071: USDC spot quote markets

- Status: Accepted
- Date: 2026-09-17

## Context

The 17 Sep portfolio-research ops run confirmed the operator's Coinbase cash/quote currency is
USDC, not USD. ThyTrader rejected `BTC-USDC` and every other `*-USDC` product because product,
strategy, research, risk, and runtime contracts hard-coded the `^[A-Z0-9]{2,20}-USD$` pattern and
`quote_currency: USD` literals. Research and paper therefore ran USD-proxy products while live cash
would settle in USDC.

Coinbase Advanced Trade lists both `BASE-USD` and `BASE-USDC` spot products. Portfolio valuation
already treats USDC at par with USD for read-only totals; execution must now accept the operator's
actual quote market end to end.

## Decision

- Accept Coinbase spot product ids ending in `-USD` or `-USDC` through one shared
  `SPOT_PRODUCT_ID_PATTERN` and `SpotQuoteCurrency = Literal["USD", "USDC"]`.
- Strategy `instrument` and `additional_instruments`, research `capital.quote_currency`,
  risk-policy `quote_currency` and `product_allowlist`, data-control watch/ingest targets, runtime
  discretionary orders, and paper/live sizing use the strategy or product quote currency instead of
  assuming USD.
- The market-data catalog lists enabled spot products whose quote is USD or USDC; EUR and other
  quotes remain excluded.
- Multi-instrument documents require every covered instrument to share one quote currency.
- Ops contract `thytrader-ops-contract-v29` advertises `spot_quote_currencies: ["USD", "USDC"]`.
  Alembic `0042` marks the contract bump. No PostgreSQL shape change is required.

## Consequences

- Operators can watch, ingest, publish, backtest, and paper-trade `BTC-USDC` and other USDC-quoted
  spot products without USD proxies.
- Live discretionary and strategy-driven paths read venue balances in the declared quote currency
  (`USDC` when the product quotes USDC).
- Portfolio total valuation still marks USDC at par with USD; depeg handling and a configurable
  valuation currency remain future work (QA P1 item 9).
- CLI/API preflight expects ops contract v29 and schema revision `0042` after rebuild.

## Alternatives considered

- Keep USD-only products and treat USDC as a portfolio display detail: rejected; it blocks the
  operator's actual quote markets and misstates cash availability at execution time.
- Accept arbitrary quote suffixes beyond USD/USDC: rejected; Coinbase spot scope stays explicit and
  fail-closed.
