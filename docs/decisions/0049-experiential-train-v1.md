# 0049: Bounded journal-evidence experiential training V1

- Status: Accepted
- Date: 2026-09-16
- Relates to: [0019](0019-ops-contract-identity.md), [0030](0030-agent-e2e-primary-surface.md),
  [0037](0037-phase-14-experiential-memory.md),
  [0046](0046-shipped-vs-remaining-0031-destination.md),
  [0047](0047-wider-fail-closed-indicator-catalog.md),
  [0048](0048-paper-deploy-fee-fields.md)

## Context

Phase 14 shipped origin-attributed journal, sentiment, and pattern **hooks**
([ADR 0037](0037-phase-14-experiential-memory.md)) with no learner. Agents still could not train a
fail-closed model from those attributed local rows. [ADR 0046](0046-shipped-vs-remaining-0031-destination.md)
restates shipped vs remaining Coinbase-first destination; it does not own a trainer.
[ADR 0047](0047-wider-fail-closed-indicator-catalog.md) owns the wider fail-closed indicator catalog;
this trainer does not change it. [ADR 0048](0048-paper-deploy-fee-fields.md) owns paper deploy fee
fields; this trainer does not change them. A sibling slice owns persisted why-trade journal kinds and
human/agent review surfaces. This slice must consume the
shared `thytrader-experiential-memory-v1` document as stored — not invent a parallel trade-reason
schema or a review UI.

Training must never bypass risk, call Coinbase, interpolate candles, weaken `--confirm` /
`--i-understand-live`, or become a secret live brain. Extra exchanges stay out. YOLO still never
covers the memory lane.

## Decision

Ship a bounded V1 trainer as `thytrader-experiential-train-v1`. Documents:

- `thytrader-experiential-model-v1` — immutable fingerprintable ranks
- `thytrader-experiential-advisory-v1` — gated research hint only

### Corpus and fail-closed evidence

Training reads origin-attributed `JournalEntry`, `PatternObservation`, and `SentimentSnapshot` rows
already stored. It does not add journal kinds. Rows with `evidence_kind=none` are skipped. Dangling
or unavailable local evidence fails the whole train. Sentiment is included only when `journal_id`
points at a selected journal.

Local evidence kinds resolve against backtests, research-run child results, dataset manifests,
deployments, and paper/live fills. Missing candles are never interpolated. The resolver never calls
Coinbase.

The minimum corpus is at least one evidenced journal or pattern. The engine is a seeded integer
ranker (not a neural net). Fingerprint is `sha256:` plus 64 lowercase hex over engine, seed, and
sorted selected ids. Retraining with the same fingerprint returns the stored row.

### Surfaces

- `thytrader-memory train|list-models|show-model` — HTTP-only; mutations require `--confirm`; YOLO
  never skips that gate
- `GET|POST /api/v1/memory/models` and `GET /api/v1/memory/models/{id}`
- `thytrader-research create-draft --experiential-model-id` — HTTP only; `--local` refuses; loads
  the model fail-closed and **merges the advisory into create-draft JSON**. It does not change
  published strategy semantics, place orders, or arm live trading

Alembic `0029` adds `experiential_models` only. Journal tables stay unchanged. Ops contract becomes
`thytrader-ops-contract-v17` with `experiential_model_engines` and `expected_schema_revision` `0029`.

## Consequences

- Operators and agents can train from attributed local journals and pass the advisory into research
  draft JSON without a live policy.
- Trade-reason kinds and review UIs remain a sibling concern; this trainer consumes whatever
  `JournalEntry` rows exist, including future kinds once that document accepts them.
- Memory mutations still never inherit YOLO. Extra exchanges stay out.

## Alternatives considered

- **Neural net or online live learner:** rejected; V1 is a deterministic integer ranker with a
  content fingerprint.
- **Duplicate journal schema or review UI here:** rejected; sibling owns why-trade records and
  review surfaces.
- **Fold training into research YOLO:** rejected; train stays on the memory lane with `hard_gate`.
- **Write advisory into strategy indicator/entry semantics:** rejected; that would make the model a
  secret live brain. Advisory is JSON beside the draft identities only.
