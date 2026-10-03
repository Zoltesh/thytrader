# 0105: Verify pinned trading rules when explicitly linking strategy clones

- Status: Accepted
- Date: 2026-10-03
- Amends: [0102](0102-explicit-paper-live-twin-links.md) (shared-snapshot requirement)
- Relates to: [0082](0082-strategy-root-mutable-strategies-auto-snapshots.md)
- Ops contract: v63; Alembic remains 0060

## Context

The handoff's Core live portfolio and its HX3 paper twin use cloned strategies. A snapshot
fingerprint covers the whole authored document, including root id, creation time, name, and
annotations. Those clones cannot share a fingerprint even when their trading rules are identical.
The existing one-snapshot link requirement therefore cannot link the intended existing twins.

## Decision

Keep identical-fingerprint pairing as the existing fast path. For different fingerprints, the
API loads both **deployment-bound snapshots**, not the mutable roots. Store-level validation
checks that each proof belongs to the deployment's exact fingerprint, hashes its canonical
content, and compares canonical rules after aligning only `strategy_id`, `name`, `description`,
`created_at`, and `metadata`. All remaining fields must match: schema, instrument(s), clocks,
data requirements, indicators and offsets, entry and filter rules, sizing, portfolio limits,
exits, and execution. A strategy edited after deployment does not change the proof.

Proof is an internal typed pair of snapshots. The HTTP request still accepts only
`counterpart_deployment_id`; clients cannot provide or override proof. Missing/unverifiable
snapshots fail closed (503); different rules fail 422. PostgreSQL revalidates proof against the
locked deployment rows before saving, preserving one-to-one conflict and worker-write isolation.
No execution state, lease, revision, capital, order, or deployment lifecycle is modified.

Each fill-comparison side adds its own `strategy_fingerprint`. The existing top-level
`strategy_fingerprint` remains the paper-side reference for compatibility, not a claim that the
identities match. Reports still select only persisted links. UI choices include opposite-mode
strategy bots on the same primary market and timeframe; the server verifies exact rule equality
before saving. Matching names or a shared product never establish equivalence.

## Alternatives

Restarting the existing paper books from the live root would discard the intended continuous
comparison and change runtime state. Editing their strategy or snapshot identity would break
content-addressed evidence. Allowing any pair without a rule proof would misrepresent experiments.
A new globally normalized strategy fingerprint would change research/run identity; comparison-only
canonical alignment leaves existing strategy fingerprints untouched.
