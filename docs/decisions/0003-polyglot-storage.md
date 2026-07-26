# 0003: PostgreSQL plus Parquet, Polars, and DuckDB

- Status: Accepted
- Date: 2026-07-26

## Context

Trading operations require transactional, restart-safe state. Historical market data and backtests require efficient columnar scans over larger datasets. One storage technology does not need to serve both workloads poorly.

## Decision

Use:

- PostgreSQL for operational records and coordination;
- Parquet for partitioned historical and derived analytical datasets;
- Polars as the primary Python dataframe/query engine;
- DuckDB for analytical SQL over Parquet and derived results.

## Consequences

- Orders, fills, runtime state, strategy versions, risk state, and audit records get transactional durability.
- Historical scans avoid forcing large datasets through PostgreSQL or pandas.
- Dataset manifests, partition rules, schema evolution, and retention must be documented.
- Docker Compose must bundle PostgreSQL and persistent volumes so installation remains straightforward.
- Operational correctness may not depend on an in-memory dataframe or DuckDB session.

## Alternatives considered

- **SQLite only:** simpler initially but a weaker fit for concurrent API/worker coordination and long-lived operational evolution.
- **PostgreSQL for all candles:** operationally familiar but inefficient and cumbersome for large portable analytical datasets.
- **Pandas:** rejected as the primary dataframe layer in favor of Polars performance and memory behavior.
