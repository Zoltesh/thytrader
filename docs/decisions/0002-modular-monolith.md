# 0002: Modular monolith with API and worker processes

- Status: Accepted
- Date: 2026-07-26

## Context

ThyTrader needs portfolio queries, backtesting, long-lived exchange WebSockets, scheduled strategy evaluation, order reconciliation, and restart-safe trailing stops. Premature microservices would increase deployment and consistency complexity, while putting all work inside HTTP handlers would make reliability and future extraction difficult.

## Decision

Build a modular Python monolith with explicit domain boundaries. Deploy at least two Python processes:

- a FastAPI process for supported user/operator interfaces;
- a continuously running worker for feeds, strategies, risk, orders, reconciliation, and ingestion.

Both processes may share application/domain packages and PostgreSQL. Core automation does not use cron.

## Consequences

- One repository and release remain easy to install and reason about.
- Worker failures and API request lifetimes are separated.
- Database coordination, ownership, and idempotency rules must be explicit.
- Domain code must not depend on FastAPI route objects or Coinbase response models.
- Components can later move to separate services or Rust when profiling and operational needs justify it.

## Alternatives considered

- **Single FastAPI process with background tasks:** too fragile for persistent trading work and deployment restarts.
- **Cron-triggered strategies:** unsuitable for WebSockets, active order state, and trailing stops.
- **Microservices from inception:** adds networking, deployment, observability, and distributed-consistency costs before they solve a measured problem.
