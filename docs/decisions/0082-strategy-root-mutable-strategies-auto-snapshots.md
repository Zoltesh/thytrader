# 0082: Strategy as the root object — mutable strategies, automatic snapshots, hard delete

- Status: Accepted
- Date: 2026-09-30
- Supersedes in part: [0005](0005-canonical-strategy-schema.md) (immutable, versioned
  documents and the draft/published/archived lifecycle), [0007](0007-immutable-research-run-specifications.md)
  (run specs naming a *published* strategy), [0080](0080-per-strategy-workspace-build-test-run-why.md)
  (`?version=` context, Draft/Published vN pills, version picker, publish and archive in the
  workspace and library), [0044](0044-parameter-sweeps-wfo-stitched-equity.md) (publishing
  derived sweep documents and `candidate_strategy_fingerprints`), [0035](0035-phase-11-research-rigor.md)
  (studies over published fingerprints), [0034](0034-phase-12-agent-orchestration-yolo.md)
  (playbook `draft/publish` step), and the append-only archive markers described in the roadmap and
  `docs/architecture/strategy-and-backtesting.md` (they never had a dedicated ADR).
- Relates to: [0033](0033-phase-10-risk-policy-registry.md) (risk-policy allocations),
  [0054](0054-trade-reason-journals.md), [0060](0060-multi-book-deployment-api.md),
  [0078](0078-live-readiness-http-ack-venue-reload-definite-rejects.md)

## Context

A strategy used to move through an explicit ceremony: create a draft, edit it, "Validate & publish
immutable version", then pick v1/v2/v3 by fingerprint in every stage. Drafts and published versions
lived in different tables, a published version could only be revised into a new draft, and the
library grouped identities by joining fingerprints at read time. The operator found the ceremony
confusing and unnecessary. The part that must survive is the honest part: every backtest, study,
and bot must record exactly which rules it used.

## Decision

**One mutable strategy.** A strategy is one row in a new `strategies` table (`strategy_id`
UUIDv7 primary key, `name`, `document`, `is_valid`, `validation_issues`, `current_fingerprint`,
`revision`, `product_id`, `timeframe`, `created_at`, `updated_at`). You edit it and save it in place.
Drafts, publish, revise, archive markers, and version numbers are removed.

- **Saving** replaces the whole document under an optimistic-concurrency `revision`. A stale save is
  HTTP 409 `strategy_revision_conflict` (with `current_revision`) and is never overwritten. The
  server forces the row's `strategy_id` and `created_at` into the document. An invalid work-in-progress
  document is saved as-is with its validation result (`validation.valid`, `validation.issues`) and
  `current_fingerprint: null`. Documents must be JSON objects of at most 256 KiB
  (`strategy_document_invalid`).
- **Starting requires validity.** Backtest, study, and deployment starts require a currently valid
  definition and fail closed with HTTP 422 `strategy_invalid` (listing `issues`) otherwise.
- **Clone** copies a strategy (valid or not) into a new identity named "<name> (copy)". **Import**
  always creates a new strategy with a fresh `strategy_id` and `created_at`.

**Automatic snapshots.** Starting a backtest, study, or deployment snapshots the strategy's current
definition into `strategy_snapshots` (`strategy_fingerprint` primary key, nullable `strategy_id`
FK `ON DELETE SET NULL`, `canonical_definition`, `created_at`). Snapshots are content-addressed and
deduplicated: saving the same rules twice and starting twice yields one snapshot. Each run or bot
stores `strategy_id` plus the snapshot `strategy_fingerprint` (the field keeps its historical name
and now always means "snapshot fingerprint"). Consumers compare a row's `strategy_fingerprint` with
the strategy's `current_fingerprint` to say **Current rules** or **Earlier edit**, and
`GET /api/v1/strategies/snapshots/{strategy_fingerprint}` returns the exact snapshot for a "What
changed" diff or to resolve old fingerprint deep links to the owning strategy.

**Canonical document and fingerprint.** The canonical strategy schema (`schema_version: "1.0"`)
keeps its structure minus the lifecycle fields: `version` and `status` are removed. Legacy input
carrying those keys is accepted and the keys are discarded, so old JSON still imports; they never
appear in canonical bytes. The fingerprint algorithm is unchanged and stable:
`strategy_fingerprint = "sha256:" + hex(SHA-256(canonical bytes))`, where canonical bytes are the
revalidated document serialized as UTF-8 JSON with sorted keys, `(",", ":")` separators, no NaN, and
optional empty blocks (`htf_filter`, `additional_instruments`, `entry.pyramiding`, absent indicator
inputs and operand series) omitted. `strategy_id`, `created_at`, `name`, `description`, and
`metadata` are part of the document, so a rename produces a new snapshot. Parameter-sweep variants
keep the base `strategy_id` (they belong to that strategy) and differ only in the swept values,
derived name, description, and metadata.

**Strategy as the root object.** `strategy_dataset_bindings`, `published_research_run_specs`,
`published_backtest_results`, `published_research_studies`, and `research_jobs` carry a
`strategy_id` foreign key to `strategies` (`ON DELETE CASCADE`). A new `research_study_strategies`
link table records every strategy a study includes (cross-market and candidate studies can span
several). `deployments.strategy_id` references `strategies` `ON DELETE SET NULL`, and
`deployments.strategy_name` captures the name at start. Reads filter by `strategy_id` on indexed
columns (`GET /api/v1/backtests|research/studies|research/jobs|deployments?strategy_id=`) instead
of joining fingerprints at read time.

**Deletion.** `DELETE /api/v1/strategies/{strategy_id}` and `POST /api/v1/strategies/bulk-delete`
(`{strategy_ids, confirm, dry_run}`, confirmation-gated, CSRF-protected in the browser, one result
per id: `deleted` / `would_delete` / `blocked` / `not_found` / `failed`) hard-delete the strategy and,
in one transaction, its snapshots, backtests, run specs, studies that include it, research jobs,
dataset bindings, and PAPER deployments with their orders, fills, positions, intents, and trade
reasons.

- Refused with HTTP 409 `strategy_has_active_deployments` (with `deployment_ids`) while any
  deployment of the strategy is running or paused. The operator stops it first.
- Stopped LIVE deployments are kept with their orders, fills, positions, trade reasons, and the
  snapshot they ran. They are detached (`strategy_id` NULL; `strategy_name` kept;
  `strategy_deleted: true` in HTTP) and shown as "<name> (deleted strategy)". Real-money records are
  never destroyed by strategy deletion. The database enforces that only stopped live books may be
  detached.
- If the active risk policy allocates capital to the strategy, the same transaction publishes the
  next risk-policy version without that allocation (`risk_policy_republished: true`), consistent
  with the registry's immutable-version rule ([0033](0033-phase-10-risk-policy-registry.md)).

**Migration 0048 (approved data wipe).** All current strategy, research, backtest, and paper
deployment data was test data. The migration creates `strategies`, `strategy_snapshots`, and
`research_study_strategies`; drops `strategy_drafts`, `published_strategy_versions`, and
`archived_strategy_versions`; recreates bindings, run specs, results, studies, and jobs with
`strategy_id` foreign keys (all rows wiped); deletes paper deployments and their ledgers; and drops
`trade_reason_records.strategy_version`. It is still safe if live rows exist: live deployments and
their ledgers are kept, their snapshots are copied into `strategy_snapshots` with `strategy_id`
NULL, their `strategy_id` becomes NULL, and `strategy_name` is captured. Because canonical bytes no
longer contain `version`/`status`, a preserved live snapshot is re-addressed: the lifecycle keys are
stripped, the fingerprint is recomputed, and the deployment and its trade reasons are updated to the
new fingerprint. The rules are unchanged. If the active risk policy has allocations for wiped
strategies, the migration publishes the next policy version without them. Market data (watchlist,
Parquet datasets, feed state), portfolio snapshots, credentials, YAML settings, audit events, and
experiential journals are kept.

**Contracts.** `thytrader-research` gains `list-strategies`, `show-strategy --strategy-id`,
`show-snapshot`, `create-strategy`, `save-strategy --revision`, `import-strategy`, `clone-strategy`,
`delete-strategy`, and `bulk-delete-strategies (--dry-run | --confirm)`; `submit-backtest` and
`submit-study` files name strategies by id. `create-draft`, `save-draft`, `import-draft`, `publish`,
and `archive` are removed. `thytrader-runtime start --strategy-id` replaces
`--strategy-fingerprint`. The playbook uses `--create-strategy` and `--strategy-id`; `--publish` is
removed. Operator `strategies` reports a `strategies[]` list instead of `drafts` and `publications`.
Operator chat tools follow the same shapes. The ops contract is `thytrader-ops-contract-v41` with
`strategy_model: ["mutable_root", "auto_snapshot", "hard_delete"]` and expected Alembic revision
`0048`.

**Engines are untouched.** The V1–V4 backtest engines keep their contracts in this change; a
follow-up unifies them. No new user-facing `vN` names are introduced.

## Consequences

- Operators edit one object and never pick versions. Evidence stays exact: every result and bot
  names its snapshot, and "Earlier edit" is a derived comparison, not a lifecycle state.
- Editing a strategy never changes a running bot. Moving a bot onto the current rules is an explicit
  managed stop followed by a new start (the UI's guided **Update bot**, with live still requiring
  the understand-live acknowledgement).
- Research evidence for a deleted strategy is gone; live money history is not. Studies that include
  a deleted strategy are deleted with it.
- Renames and description edits create new snapshots. Old fingerprints from before migration 0048 do
  not resolve (the data was wiped), apart from re-addressed live snapshots.
- Agents and scripts that published, archived, or started by fingerprint must switch to strategy
  ids; the ops-contract bump makes a stale CLI or image fail closed.

## Alternatives considered

- **Keep publish but auto-publish on start.** Rejected: it keeps the version vocabulary and the
  two-table split that confused the operator, for no extra honesty over content-addressed snapshots.
- **Per-version rows (strategy_versions with v1/v2/…).** Rejected: version numbers invite "which
  version is live?" reasoning; fingerprints already identify exact rules, and deduplication makes
  numbered rows redundant.
- **Soft delete / archive.** Rejected: archive markers hid clutter without removing it and never let
  the operator clean up test data. Hard delete with a live-ledger carve-out is simpler and keeps the
  only records that must survive (real money).
- **Refuse deletion when a risk-policy allocation references the strategy.** Rejected in favor of
  publishing the next policy version in the same transaction, which follows the registry's
  immutable-version rule and avoids a manual extra step.
