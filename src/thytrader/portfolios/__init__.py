"""Portfolios: sleeves of strategies under shared limits, with manager settings (ADR 0088).

A portfolio is either paper or live, never mixed. Each sleeve holds one strategy with a
capital weight; the remainder is cash. This package holds the domain rules, the store
contract, the portfolio backtest that combines independently simulated sleeves, and the
confirmation-gated ``thytrader-portfolio`` CLI. Nothing here deploys or places orders.
"""
