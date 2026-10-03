# 0102: Explicit paper/live deployment twin links

- Status: Accepted
- Date: 2026-10-03
- Amends: [0097](0097-runtime-parity-and-observability.md) and
  [0098](0098-library-views-book-marks-portfolio-fills.md): supersedes implicit newest-by-snapshot
  pairing and ships their explicit-link deferral.
- Relates to: [0030](0030-agent-e2e-primary-surface.md),
  [0082](0082-strategy-root-mutable-strategies-auto-snapshots.md),
  [0019](0019-ops-contract-identity.md)

## Context

Several stopped or running bots can share a rules snapshot. Selecting the newest paper and
live books for that fingerprint can compare unintended deployments and silently change the
comparison when another bot is created. Operators need to select and retain the intended pair.

## Decision

An explicit, one-to-one `deployment_twin_links` relationship stores the paper deployment id,
live deployment id, and UTC `linked_at`. Both books must be strategy deployments with the same
nonempty immutable `strategy_fingerprint`, primary market, and timeframe. Strategy root ids,
status, portfolio membership, cash, fees, and start dates may differ. Stopped books remain
comparable. Links never change deployment revisions, worker leases, arming, lifecycle, or orders.

`GET /api/v1/deployments/{id}/twin` returns `{deployment_id, twin}` with either the pair or null.
`PUT` accepts only `{counterpart_deployment_id}`. The same pair is idempotent and retains its
timestamp. A member already paired differently returns 409; replacement requires unlinking first.
`DELETE` requires the expected `counterpart_deployment_id` query parameter: absence is idempotent,
but a different current partner returns 409. Missing deployments return 404, incompatible books
422, and unavailable storage 503. Both members expose the same pair. The existing installation
authentication and browser CSRF boundary protects writes, and runtime audit events record only
the two ids. As with existing runtime control, audit append follows the metadata transaction;
an uncertain response requires reading the link before retrying.

The runtime CLI provides `show-twin ID`, `link-twin ID --counterpart-deployment-id ID --confirm`,
and `unlink-twin ID --counterpart-deployment-id ID --confirm`. The mutation gate is unconditional:
YOLO never skips it. No live acknowledgement is required for these metadata-only controls.
Bot detail offers matching opposite-mode candidates and confirms linking/unlinking in a dialog.
A failed write blocks further edits until a fresh link read succeeds.

PostgreSQL locks both deployment rows in sorted UUID order inside a short transaction, validates
their comparison facts, and applies the relationship. A primary key on the paper member and a
unique live member prevent competing partners. Foreign keys cascade when deployments are
deleted. The separate table prevents stale worker snapshots from erasing links. In-memory
operations validate and mutate without yielding, preserving the same conflict semantics.

The operator and per-portfolio fill reports use saved links only, newest-linked first, at most
10 comparisons. Multiple pairs may share one snapshot. Fill statistics and paper/live recognition
timing are unchanged. Unlinked bots have no comparison; unavailable link storage produces a
warning and no inferred pairs. Alembic `0060` creates an empty table with no guessed backfill.
Downgrade refuses while links exist. Ops contract `thytrader-ops-contract-v62` advertises
`runtime_observability: explicit_deployment_twins` and schema revision `0060`.

## Consequences and alternatives

- Operators select historical or current comparison books once, including portfolio sleeves,
  and the choice survives restarts and later deployments.
- Existing installations must deliberately link intended bots before comparisons appear.
- Rejected: explicit links plus an implicit fallback. Unlinked or unreadable metadata could still
  select the wrong pair and conceal an operator's deliberate unlink.
- Rejected: mutable partner fields on both deployments. Stale worker saves could overwrite them,
  and keeping both fields consistent would complicate the runtime revision contract.
- Study planning, quiet-bar behavior, automatic twin deployment, and order management remain
  separate work.

Follow-up: [0105](0105-rule-equivalent-clone-twins.md) allows different snapshot identities only
when the server verifies identical pinned trading rules across clones.
