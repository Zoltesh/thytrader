# 0101: Atomic portfolio creation with initial sleeves

- Status: Accepted
- Date: 2026-10-03
- Amends: [0088](0088-portfolio-model-and-portfolio-backtest.md) (portfolio creation) and
  [0094](0094-research-honesty-and-agent-ergonomics.md) (supersedes the deferral of `create --file`
  with sleeves)
- Relates to: [0030](0030-agent-e2e-primary-surface.md),
  [0091](0091-portfolio-deployment-limits-and-manager-proposals.md),
  [0019](0019-ops-contract-identity.md)

## Context

An agent with a complete portfolio definition currently creates an empty portfolio, then adds
its sleeves in another mutation. A refused second call leaves an empty portfolio and requires
cleanup. The existing batch-add planner already checks the entire sleeve set before persistence.

## Decision

Extend the existing `POST /api/v1/portfolios` request with optional `sleeves`, reusing
`SleeveBatchItem` (`strategy_id`, `weight_fraction`, optional `note`). `thytrader-portfolio create
--file portfolio.json --confirm` forwards the complete validated document in one POST. Existing
flags override portfolio settings. Omitted or empty sleeves preserve empty creation.

Request validation rejects malformed values, duplicate strategies, and more than 32 sleeves.
Creation and batch-add share one pure sleeve planner: strategies must exist and have readable
markets in the portfolio's quote currency; exact decimal sleeve weights plus reserve cannot
exceed 1. Existing draft-strategy issue semantics remain unchanged; deployment and backtests
still perform their own admission checks.

PostgreSQL locks referenced strategy rows `FOR SHARE` in sorted id order within the creation
transaction, matching batch-add and preventing strategy changes or deletion during validation
and persistence. Only after planning succeeds does it insert the portfolio and all sleeves,
then append `created` and one `sleeve_added` entry per sleeve. The portfolio and every entry use
**revision 1**. Any validation or database failure leaves no partial rows. Request order is
preserved with ascending sleeve ids. No migration is required; Alembic remains `0059`.

Creation grants no deployment, live-arming, or order authority, even when the definition's mode is
live. The portfolio lane still requires `--confirm`; deployment remains a separate runtime action.
Ops contract `thytrader-ops-contract-v61` advertises `create_with_sleeves` alongside `batch_add`.

## Consequences and alternatives

- Agents can submit a complete definition without cleanup after a failed sleeve add. Subsequent
  edits begin with revision 1 and use the existing revision guard.
- A shared planner keeps batch-add and creation aligned without expanding runtime authority.
- Rejected: orchestrating create then add inside the CLI. Two HTTP transactions cannot provide
  all-or-nothing persistence and leave cleanup races.
- Rejected: a new import endpoint or file format. The existing create body can grow compatibly,
  while strategies remain separately authored objects referenced by id.
