# Safety alerts release-review follow-up — 2026-10-06

This follow-up addresses all five release-blocking concerns in the review of
`d4e3897`. It is local work for lead integration, not a deployment or production
verification. The earlier completion note is historical and must not be read as
verification of the stronger guarantees below.

## Changes and regressions

1. **Evidence-scoped recovery.** Gathering produces positive findings and exact
   `(code, subject)` checks with complete evidence. Unknown/unavailable/stale or
   explicitly partial snapshots, occupied/empty inventory inconsistencies,
   failed reads, warming/missing/gapped/insufficient candles, and subset deployment
   inventories cannot clear inventory-dependent alerts. Complete row checks can
   recover independently. Authoritative full deployment inventory can prove
   intentional deletion; alert history survives it (`ON DELETE SET NULL`). A
   triggered-unfilled stop stays open through price rebound until durable
   terminal/fill/removal evidence clears that order. Trigger scanning excludes
   bars beginning before order creation. A crossed creation-spanning bar is
   ambiguous, not a positive trigger or a recovery proof; a whole untouched
   creation bar can prove a negative. Missing candle history preserves both
   uncovered and unknown-cover alerts. Implausibly future leases indicate
   unknown age/possible clock skew, not successful protection maintenance.
2. **Atomic, ordered observations.** Findings and explicit clears are applied in
   one transaction. Durable per-check watermarks reject older/replayed batches,
   including an old failure after newer recovery. Failure wins equal-timestamp
   ties. Worker observations use cycle-start time, not late completion time.
   In-memory and PostgreSQL implementations share these semantics. Safety uses
   the complete open inventory, independently of the bounded report display;
   report totals/status also use all open rows and prioritize critical display
   rows with a truncation warning.
3. **Fenced pause and restart safety.** Supervision acquires the existing worker
   lease, rereads eligibility/revision/strategy identity, and writes through
   `RevisionFencedStore`. Lease skips, revision races, deletes, stops, operator
   pauses, unrelated mismatches, and strategy changes cannot be overwritten.
   Persisted error counts survive restart and a crash between threshold recording
   and pausing. Held pause observations do not add fabricated errors. Ambiguous
   non-raising passes are not success: recovery requires cursor advancement on
   the same snapshot, and a held supervision pause remains open until manual
   clearing plus verified work. Real paper-worker regression verifies that a
   persisted pause still executes a risk-reducing stop without opening entries.
   A PostgreSQL regression reconstructs both repositories and verifies the
   actual fenced pause, revision increments, and retained error count.
4. **Durable, isolated delivery.** Claim before sending with CAS, a 60-second
   expiry, bounded attempt slots, and a 15-second send timeout. Only the current
   token can acknowledge; retries reuse the alert UUID. Disabled delivery
   persists `skipped` without spending attempts and can send after enabling.
   Provider errors/result details cannot expose a destination or secret.
   Dispatch runs in a separate CLI worker task, not within execution cycles.
   Exactly-once external delivery is not claimed: a send/ack crash can duplicate
   a webhook; recipients must deduplicate the stable UUID. Bounded retries can
   exhaust without delivery; no eventual external receipt guarantee is made.
5. **Honest tests and documentation.** Added focused unknown/partial/recovery,
   stale/tie/concurrent-batch, pause-race/restart, delivery-claim/failure/bounded
   retry, deleted-history, and PostgreSQL migration-roundtrip regressions.
   Updated ADR 0115, operator skill/reference text, user operations, and agent
   integration documentation. Corrected the old Python 3.14 exception-syntax
   assertion and explicitly named the previously excluded strategy-backtest
   integration module in the historical note.

## Exact verification

Private disposable PostgreSQL 17 was used only on loopback port **26466**, database
`alerts_test`, container `tt-alerts-followup-test-db`. The URL below was set to that
artificial test-only database, never a production URL. The alerts fixture refuses
other hosts/ports, and applies the actual Alembic chain. No credential files or
real `.env` values were read.

```bash
THYTRADER_ALERT_TEST_DATABASE_URL="$ALERT_TEST_URL" \
THYTRADER_TEST_DATABASE_URL="$ALERT_TEST_URL" \
uv run pytest tests/alerts/ tests/execution_worker/ \
  tests/persistence/test_postgres_leases.py \
  tests/api/test_operator_alerts.py tests/operator_diagnostics/ -q --tb=short
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

Results: **258 passed, no skips, 11 warnings** (existing Alembic `path_separator`
deprecation), in 7.69 seconds. Ruff lint passed, formatting passed (**878 files
already formatted**), and ty passed. Earlier focused expanded selection passed
184 tests; subsequent PostgreSQL pause, real paper-exit, and future-skewed-lease
regressions, plus creation-bar ambiguity/recovery checks, are included in the
final 258-test selection.

No full repository suite, frontend checks, or concurrent full graph rebuild are
claimed for this follow-up. Those checks are serialized in the integrated lead
tree. The frontend was not changed.

## Graph and integration boundaries

Reused the existing `tt-review-alerts` GitNexus index. Context/query/upstream
analysis covers the alert service, stores, gathering/reporting, worker cycle,
supervision pause, and existing lease/revision-fence APIs. Concrete store method
resolution can be ambiguous/UNKNOWN through protocol dispatch; source usages and
executed in-memory/PostgreSQL tests, not empty graph results, are authoritative.
The existing index reports behind HEAD; the lead must reindex the integrated tree
before claiming fresh coverage of new methods and helpers.

`detect-changes --scope all --repo tt-review-alerts` reports **24 files, 116
indexed symbols, 13 processes, HIGH risk**. The CLI human summary is abbreviated;
raw MCP `detect_changes` with `scope=all` and the explicit worktree was also read
in full (116 returned symbols, 13 returned processes, no partial/truncated flag).
The affected flows concern gathering, alert persistence/redaction, and worker
startup/credential reload/settings. The cached source diff and targeted tests
verify that lifecycle helpers and credential/settings behavior were not rewritten.
Stale line mapping includes old/deleted method names and some shifted, unchanged
neighbors; it is not fresh coverage of new code.

The route map locates `GET /api/v1/operator/alerts` and its `web/src/lib/alerts.ts`
consumer. `shape_check` finds no comparable shapes: **UNKNOWN coverage, not a
clean compatibility proof**. Executed API/contract tests validate the actual
report. Structural `check` reports two existing import cycles: worker service ↔
venue (the reverse edge is type-checking-only) and frontend deployment detail ↔
strategy workspace. These edges were not changed; no new structural all-clear is
claimed. Raw results are retained under `/tmp/tt-implementation/alerts-followup-*`.

Only the reserved, not-yet-deployed migration **0066** was extended, adding
`operator_alert_checks`, claim token/expiry, and retained deleted-book history.
The parent remains `0064` in this checkout; lead alone rechains it around risk
migration `0065` and owns shared release version/schema/index reconciliation.
No new migration revision, global ops-contract version, expected schema revision,
JSON report-schema index, or ADR index was changed by this follow-up. An already
applied older 0066 would require a separately coordinated forward migration;
editing a migration does not rerun it on an existing database.

The lifecycle lane owns window-warming and strategy-advancing helpers. Its shared
warming exception must be handled as no evidence, not successful work. This lane
does not rewrite those helpers or deployment lifecycle APIs. The external
coordination note is `/tmp/tt-implementation/agents/alerts-interface-note.md`.

Lead alone integrates, pushes, merges, and deploys. No production database,
service, policy, deployment, strategy snapshot, credential, or live mutation was
used or altered. The private test container is removed after verification.
