# Alert/protection integration follow-up — 2026-10-06

The integrated alert and protection implementations require a shared evidence contract, not only passing lane tests.

## Verified integration corrections

- A fully shaped local stop with no genuine venue receipt must not resolve an existing `STOP_UNCOVERED` incident merely because the candle range is complete. Unknown current coverage now withholds recovery authorization; the prior incident and current unknown-evidence finding remain visible.
- Alert protection findings use the supplied evaluation clock consistently. A fixed-clock regression previously depended on wall time through `book_protection_status`.
- The worker keeps its cycle-start timestamp as the monotone alert observation watermark, but evaluates freshness after the cycle's venue reads. Otherwise a newly received status can look future-dated against the cycle's older start. Alert observation timestamps are logical cycle observations, not venue receipt timestamps.
- The creation-bar regression now supplies executable, matching stop geometry and explicit identified venue receipt evidence. Local `updated_at` is not a substitute. Both receipt absence and ambiguous creation-bar crossings are covered.
- PostgreSQL alert tests use the standard explicitly selected test URL and an owned disposable database per test, including migration downgrade/restart tests. Loopback/test-name guards reject production port5439. No bespoke alert port, skipped suite, shared database downgrade, or production behavior weakening is needed.

## Verification at this integration step

`tests/fleet_control`, `tests/alerts`, `tests/execution_worker`, `tests/runtime_control`, PostgreSQL leases and schema metadata, operator alerts, and generated operator schema: **272 passed**, no skips, 11 existing deprecation warnings. Both test database settings pointed to disposable PostgreSQL on loopback25439. Ruff and ty passed.

GitNexus query/context/impact and complete raw uncommitted change output were inspected. The available index predates these follow-ups, so its seven mapped symbols/three flows and MEDIUM label are incomplete historical evidence, not a release safety rating. In particular, line movement and newly introduced checks are not fully mapped. Final integrated reindex, full suites and independent review remain required.

No production code deployment, migration, bot action, credential/policy change, or new notification destination was performed by this correction.
