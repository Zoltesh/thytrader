# ADR 0122: New paper books default to the account's Coinbase fee rates

- Status: Accepted
- Date: 2026-10-07
- Supersedes: the omitted-rate default of [ADR 0048](0048-paper-deploy-fee-fields.md)
- Related: [ADR 0090](0090-research-correctness-optional-take-profit-diagnostics.md),
  [ADR 0114](0114-readiness-preflight-and-venue-reconciliation.md)

## Context

A paper start that omitted fee rates stored nothing, and the ledger applied the documented
`0.001` maker / `0.002` taker assumptions. The browser start and ticket forms also prefilled
those values and sent them explicitly whenever the account suggestion had not loaded, and
"Update bot" copied the old book's rates forward. The account in use pays `0.005` / `0.009`
(Coinbase Intro tier), so readiness reported 13 paper books modeling fees up to 4.5 times
cheaper than live fills, making paper results look better than live could achieve.

## Decision

1. A new paper book that omits both fee rates stores the account's own Coinbase rates, the
   `suggested_*` rates `GET /api/v1/fees` reports with `suggestion_source=coinbase_account`.
   This applies to `POST /api/v1/deployments`, portfolio starts (each new sleeve book) and a
   discretionary ticket that creates a new paper book. The rates are persisted on the book, so
   later tier changes do not rewrite an existing book's history.
2. When the account rates cannot be read (demo or missing credentials, a failed Coinbase
   read), the start is refused with HTTP 409 (`paper_fees_unavailable` for portfolios) and no
   book is created. The message names the explicit-rate alternative. No schedule band, cached
   value or documented default is substituted.
3. Explicit rates (both maker and taker) are used as given and skip the account read. A single
   rate remains a validation error. Live books never take paper rates.
4. A portfolio start reads the account once and only when it creates at least one new sleeve
   book, so re-attaching existing books never depends on Coinbase availability. A reused
   discretionary book keeps its stored rates.
5. The browser leaves fee fields blank until the account suggestion loads and omits blank
   fields, so the server applies point 1 or 2. "Update bot" omits rates so the replacement
   takes the account's current rates.
6. Existing books are unchanged. Books stored without rates keep the ledger's documented
   assumptions for their history; readiness continues to flag paper books whose stored rates
   are cheaper than the account's.

## Consequences

- Paper performance models what the account actually pays at start time.
- Installations without Coinbase credentials must pass explicit paper rates.
- Internal callers that pass no account source (tests, the service layer used directly) keep
  the previous omitted-rate behavior; every HTTP start surface supplies the account source.
