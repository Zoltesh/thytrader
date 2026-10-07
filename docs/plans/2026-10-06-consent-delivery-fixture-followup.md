# Consent, delivery ownership, and integrated fixture follow-up

This is an integration checkpoint, not release certification or a deployment record.

The independent controls/alerts review at `1161911` reproduced two further defects:

- A preview started before confirmation could complete during the dialog, replacing
  the target or inhibition revisions submitted after the person had reviewed older
  state. Backend CAS alone cannot protect consent if the browser replaces that consent.
  `FleetControls.svelte` now snapshots the reviewed preview, invalidates pending
  preview generations when opening confirmation, rejects obsolete results/errors,
  and retains the same snapshot and idempotency key for a failed request's retry.
  Action, mode, target revisions, and inhibition revisions cannot be silently refreshed.
- A disabled notification provider could clear an enabled dispatcher's active claim,
  allowing another sender to claim before the original TTL. Both stores now apply
  the active-owner exclusion to disabled and enabled claims alike. Separate PostgreSQL
  engines and the memory store exercise both successful and failed owner acknowledgements.
  Disabled configuration remains explicit; an in-flight attempt retains its actual
  provider/owner until completion or expiry. This is not external exactly-once delivery.

The latest full Python run at `6576811` had 3,378 passes and five failures. Three
were logging assertions after an alert fixture invoked Alembic's logging reconfiguration
inside the pytest process. Migrations now use isolated subprocesses through the shared
helper, with dotenv disabled and only a fixed PATH and explicit test database URL in
its environment. Parent-process patches are not assumed to protect a subprocess.

Two retained-risk fixtures claimed a -50 round-trip while retaining pre-trade cash.
The synthetic legacy fixture now supplies the already-applied cash movement (-50 live
zero-based PnL / 9,950 paper cash), while intentionally delaying its second fill row.
Before the missing row arrives, accounting remains unknown. After arrival, complete
closed economics prove the genuine opening balance and the retained daily loss survives
an explicit latch reset. The migration still compares all preexisting recorded fields;
no production financial row or published limit was altered.

Verification so far: the affected alerts, real isolated PostgreSQL alerts/retention,
Coinbase adapter, and ingestion test files passed together (83 tests). Ruff, format,
typing, and Svelte checking passed. Deferred real-browser preview regressions are added
for flatten and rearm, including identical retries; their isolated browser run is a
remaining checkpoint at this commit. A final complete integrated run remains required.

Graph context/impact and raw change detection were inspected. The historical index
mapped only 13 symbols and no affected processes across these eight code/test files;
its stale line mapping misses the new claim methods and Svelte closures. Its LOW label
is **not** a safety rating. Current source inspection, PostgreSQL transitions and real
browser regressions are necessary; final current graph reconstruction is still pending.

The separate per-product runtime/projection contradiction and portfolio-reporting
follow-up remain owned by the reporting integration lane. Do not infer that these
corrections certify the rest of the independent review findings.
