# Research workflow improvements from the October campaign

The operator campaign completed 1,322 historical evaluations, but slow result reads,
inconsistent product availability and ambiguous historical protection summaries made
the work unnecessarily difficult. This plan implements the seven improvements the
user approved. Existing deployments, frozen strategy rules and risk policy are not
retuned as part of this work.

## Delivery and verification

1. Give bounded result reads and paginated bulk exports a publication-integrity path
   that does not transfer full ledgers or reload historical datasets. Preserve full
   artifact verification for full result reads. Persist derived metrics at publication;
   explicitly disclose unavailable historical metrics.
2. Normalize authoritative product rows before aliases, share a bounded fresh catalog
   snapshot, and verify an omitted product through the provider before rejecting it.
3. Explain protective cancellation/replacement separately from entry cancellation,
   including old/new protection and linked orders; preserve historical journals.
4. Expose exact fee-aware entry economics and an optional snapshotted minimum net
   target-return guard shared by research, paper and live.
5. Persist research campaign manifests, pinned inputs and child-job/result identities;
   provide bounded status and exports through HTTP, CLI and the UI.
6. Add explicit deterministic maker-fill and entry-latency stress assumptions to the
   existing simulator. Default fills and existing fingerprints stay compatible.
7. Track frozen prospective evaluation windows, waiting-for-data, sample requirements
   and review outcomes durably. Forward validation remains research-only.

Each slice includes focused regression tests, relevant PostgreSQL/API tests, operator
skills and user documentation. Completion requires Python and frontend quality gates,
GitNexus change/structure review, and real HTTP/CLI checks of the upgraded instance.
Integration tests use an isolated database and never inherit the checkout's secrets or
operate its bots. Real instance checks preserve bot lifecycle/rules and risk policy.

## Status

All seven slices are implemented and verified. Operator skills, HTTP/CLI discovery,
the research UI, report contracts and migrations ship in the same change.

Verification on 2026-10-04:

- Final full Python suite with the isolated PostgreSQL database: 2,822 passed.
  Running-instance testing caught a missing installation header on the read-only
  economic POST; its regression test uses the real ASGI app with its trust boundary
  enabled. Additional real-loop vectors verify observed live fees reach repricing,
  missing tiers block replacements, and below-hurdle simulated reprices expire.
- Frontend: 431 unit tests and 229 browser tests passed, with lint, strict typing
  and production build passing. A Home hydration timeout passed three targeted
  reruns and then the complete browser suite passed without retries.
- Actual PostgreSQL tests exercise concurrent campaign refresh, one-time child
  publication, real worker execution, process restart, frozen deadlines and expiry;
  result projection tests reject altered publication and source bytes.
- The supported Compose upgrade applied revisions 0062/0063. The API, database,
  portfolio worker, market-data worker, execution worker and research worker are
  healthy, with ops contract v66 and schema 0063. Read-only comparison confirms
  all 36 deployments retain their identity, mode, status, rule snapshot and
  lifecycle command; the risk-policy fingerprint is unchanged. The 17 paused
  deployment findings predate this upgrade; reconciliation is healthy.
- A 365-trade legacy result returned its bounded summary in 3.65 seconds including
  CLI startup. A 100-result bulk export completed in 14.18 seconds, without ledger
  or Parquet transfer; legacy derived-metric omissions are explicitly reported.
- Native historical campaign `01a108ce-5e40-7159-b9ea-48f7ded803e6` ran the exact
  frozen AAVE lead on July–September Coinbase data. Baseline: 5 closed trades,
  +8.11% net; delay/penetration/half-fill stress: 4 trades, +2.74%. Both correctly
  remain `insufficient_sample` under the frozen ten-trade gate.
- Native prospective campaign `01a108cd-864c-715e-b1e5-c1d9f0da661c` preserves the
  five previously frozen imported snapshots in ten baseline/spread-stress cases.
  Its 2026-10-05–2027-04-03 window is genuinely future data, so all ten wait with
  no child jobs. Both native campaigns survived a full supported restart with
  manifests and child identities unchanged; their HTTP CSV exports returned the
  expected two and ten rows. Research creation without `--confirm` is rejected.
- Actual authenticated economic preflight reports a 100 → 100.5 maker target at
  a 0.005 maker fee as −0.5025 net quote profit after both fees.
- GitNexus embeddings/PDG are refreshed. The complete staged change analysis is
  reviewed, including the shared strategy/runtime contracts. Its critical risk
  reflects those shared surfaces; the new guards and stresses are opt-in and
  default fingerprints are regression-tested. The two existing dependency cycles
  are unchanged; no new cycles were introduced.

Raw verification artifacts are retained in
`/tmp/thytrader-research-improvements/`. Future market outcomes are not yet validated.
Campaign scheduling uses approved research authority only and never promotes a
strategy or changes trading controls automatically.
