# 0098: Library origin views, consistent paper protection, last-bar book marks, and per-portfolio fill comparisons

- Status: Accepted
- Date: 2026-10-02
- Amends: [0094](0094-research-honesty-and-agent-ergonomics.md) (the library adds an `origin`
  filter beside `tag`), [0097](0097-runtime-parity-and-observability.md) (`protection_status` now
  agrees with `position_state` for paper books, and the paper/live fill comparison is also served
  per portfolio), and [0058](0058-protection-lifecycle-accounting.md) (a paper book's synthetic
  stop counts as cover)
- Relates to: [0087](0087-per-bar-decision-timeline.md) (marks come from the decision journal),
  [0091](0091-portfolio-deployment-limits-and-manager-proposals.md); ops contract v58 (no migration)

## Context

Four operator-facing gaps showed up while agents ran research next to live and paper bots:

1. **Research buried the operator's strategies.** The library sorts by last update. One agent
   research burst (strategies tagged `claude-research` or `research-*` in `metadata.tags`) pushed the
   operator's own strategies off the first pages. The ADR 0094 `tag` filter matches one exact tag, so
   it cannot say "everything except research".
2. **`protection_status` disagreed with `position_state` on paper.** A paper book with a resting
   take-profit read `unprotected` on list and summary reads, while `position_state` read
   `open_protected`. Summary reads carry open orders but no intents, so the resting take-profit had
   no purpose to match. Live books with a resting take-profit limit had the same blind spot.
3. **Open books showed no unrealized PnL or age.** Bot detail and Portfolio sleeve rows showed the
   state, but not how the open book stood or how long it had been held.
4. **The paper/live fill comparison lived only in the operator report.** ADR 0097 added it to the
   `portfolios` report, but the Portfolio page could not show it without loading that whole
   report.

## Decision

### Library origin

`GET /api/v1/strategies` takes `origin=operator|research|all` (default `all`), and it combines with
`tag`. `research` keeps strategies whose `metadata.tags` hold `claude-research` or any tag that
starts with `research-`. `operator` keeps every other strategy. PostgreSQL evaluates it with one
lax SQL/JSON path, so a missing or malformed `metadata.tags` matches nothing. `total` and the cursor
cover the matches. `thytrader-research list-strategies --origin` sends the same filter.

The Strategies page gets a Mine / Research / All segmented control. It opens on Mine and remembers
each viewer's choice in browser storage, which is only a convenience. Row tag chips filter within
the current view, and research tags render in the info tone.

### Paper protection is cover

An open paper book is `protection_status: covered` on every read, full or bounded. The worker
enforces its stop synthetically on every closed bar, and any take-profit rests in the paper broker.
This is the same rule `position_state` applies, so a non-exiting open book's `position_state` now
always follows its `protection_status` (`covered` maps to `open_protected`, `unprotected` to
`open_unprotected`, and `unknown` to `open_unverified`).

For live books on bounded reads, an active closing-side order whose intent is not in the snapshot
counts as protective. Only exits and protection reduce an open book. An opening-side order never
counts.

### Last-bar book marks

Deployment reads (`GET /api/v1/deployments/{id}`, summary and full) add `mark_price`, `marked_at`,
and `unrealized_pnl` to each `positions[]` row and to the compatibility `position`. The portfolio
deployment read (`GET /api/v1/portfolios/{id}/deployment`) adds `books[]` to each sleeve bot. Each
book carries side, quantity, entry, stop, target, entry bar, and `position_state`, with the same
mark fields.

The mark is the close of the newest bar the bot evaluated for that product, read from the decision
journal. It is the price the worker itself last saw, so a read never calls the venue or market
data. `unrealized_pnl` is gross: the signed quantity times the move from the entry price, before
exit fees. Without a journaled close, all three fields are null, and nothing is estimated. Action
responses (start, pause, and the rest) list the books without marks.

The UI shows each book compactly on bot detail (Unrealized and Held columns, plus a uPnL line on the
Position card) and on Portfolio sleeve rows: a state chip, uPnL, time held, and entry / SL / TP.

### Per-portfolio fill comparison

`GET /api/v1/portfolios/{id}/fill-comparisons` returns `{portfolio_id, comparisons, warnings}`. The
rows are the ADR 0097 `paper_live_fill_comparisons`, limited to twins whose paper or live book is a
sleeve bot of that portfolio, newest first, at most 10. The operator report is unchanged. The
Portfolio Sleeves tab shows a "Paper vs live" panel when a twin exists. Each twin gets a paper row
and a live row: fill rate as a small bar with the count, the average fill against the limit in bps
(positive is worse), and the median wait. A sentence states the median wait gap. Sleeve rows with a
twin link to the panel.

## Alternatives considered

- **Client-side origin filtering.** Filtering one page in the browser breaks paging and totals. A
  burst of research would still produce empty first pages.
- **A `tag_not` / tag-prefix query.** This is more general, but agents and the UI need exactly one
  question ("mine or research?"). A named `origin` keeps the research convention in one place.
- **Marks from market data on every read.** That is the same source as the operator `performance`
  report, but it means a Coinbase market-data call on every polled bot or portfolio read. The
  journaled close is local and matches what the worker acted on.
- **Deriving unrealized PnL from persisted `performance_equity`.** Cash and equity can be stamped
  at different moments, and the result cannot be split per book. It could show a wrong number
  between bars.

## Consequences

- Ops contract `thytrader-ops-contract-v58`: `strategy_library` adds `origin_filter`, and
  `runtime_observability` adds `paper_protection_covered`, `book_marks`, and
  `portfolio_fill_comparisons`. Alembic stays `0059`.
- Readers that keyed on paper `protection_status: unprotected` now see `covered`. Live semantics
  are unchanged on full reads.
- A mark can be up to one bar old. The UI's tooltip names the bar close it used.

## Deferred

- Fee-adjusted (net) unrealized PnL per book.
- Origin counts on the segmented control (one extra request per view).
- An explicit paper/live link instead of fingerprint matching (still from ADR 0097).
