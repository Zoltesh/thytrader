"""Broker-neutral trading model shared by execution, risk, portfolios, and operators.

Deployments, intents, orders, fills, positions, ledgers, protection and exposure state,
lifecycle predicates, and the execution store contract with its in-memory store. Nothing
here places orders; the execution engine above does.
"""
