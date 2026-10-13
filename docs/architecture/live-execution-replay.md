# Offline execution replay

Decision: [ADR 0137](../decisions/0137-offline-live-execution-replay.md).
Commands, privacy and capture operation:
[canonical operator reference](../../skills/thytrader-runtime/references/live-execution-replay.md).

## Boundary and current capability

`thytrader-replay` is a file-only CLI, separate from HTTP lane clients. It does not construct
application settings, credentials, a network broker or PostgreSQL connection. `export` publishes
normalized evidence; `inspect` verifies its integrity and discloses recorded gaps; `run` invokes
existing execution logic with scripted inputs in an isolated in-memory store. None can send real
orders. There is no replay API, UI, deployment repair or trading action.

Two operation kinds are supported in supplied steps:

- `closed_bar`: runs `process_closed_bar` with the recorded strategy, book and evaluation inputs.
- `reconcile`: runs `reconcile_open_orders` with the recorded book and broker tape.

Each step is seeded from its own `before` snapshot; an array of steps is not an automatically
chained simulation. Prospective worker capture currently publishes one live closed-bar step per
package, after ordinary worker reconciliation and quote/window preparation. It does not capture
the initial pre-bar reconciliation or all dynamic risk/context reads. Synthetic reconciliation
fixtures exercise the actual offline reconciliation path without claiming historical completeness.

## Versioned portable documents

The authoritative field/type inventory is emitted by the installed CLI:

```bash
uv run thytrader-replay schema --kind package
uv run thytrader-replay schema --kind manifest
uv run thytrader-replay schema --kind report
```

The contracts are `thytrader-replay-v1`, `thytrader-replay-manifest-v1` and
`thytrader-replay-report-v1`, respectively. JSON Schema exposes wire shape; it does not replace
semantic validation or grant trading authority. Financial values cross the wire as exact decimal
strings, not binary-float JSON numbers. UTC timestamps and collection order are evidence. Integer
nanosecond timings/counts are not financial decimals. Unknown fields, ambiguous duplicate input
keys and forbidden secret transport fields are rejected on normalized input export.

A package contains:

| Evidence | Meaning |
| --- | --- |
| `evidence_kind` | `synthetic`, `prospective` or `historical`; a label is not completeness proof |
| `source` | Execution/research build facts; `null` remains unknown |
| `strategy`, `original_strategy` | Deployed snapshot and optional original research rules, independently qualified |
| `steps[].before` | Exact initial deployment/book, intents, orders, fills and positions |
| `steps[].product`, candle windows | Product precision and selected primary, HTF, indicator-timeframe and reference inputs |
| Portfolio, marks, live base availability, fee profile, risk policy, entry permission | Inputs supplied at the captured orchestration boundary, not a complete account audit |
| `runtime_inputs` | Ordered clock/UUID values consumed by the existing execution paths |
| `broker_events` and lookup capability | Ordered normalized requests, responses/fills, failure categories and method spans |
| `expected` | Captured cash, phase, positions, orders and fills to compare with recomputation |
| Optional `ledger` | Starting cash, chronologically ordered unique fills and observed ending cash |
| Timing and `unavailable_reasons` | Measured scopes and explicit missing evidence, never synthesized zeros |

The package validator checks strategy/book fingerprint agreement and primary completed-window
causality, continuity and OHLCV semantics. Additional clock/reference inputs must retain their
product/clock identity; consumers still enforce their evaluation requirements. Preserving supplied
inputs does not establish that a missing original dataset or external risk read was captured.
Clock and broker tapes are consumed in order and must be completely consumed. A changed request,
extra request, wrong event kind or leftover tape is evidence rejection, not permission to fall
back to a venue read. Recorded timeout/refusal categories remain recorded failures, not evidence
that an order failed at the venue or permission to retry.

Prospective execution build identity hashes the installed ThyTrader Python source path/digest
inventory. It is not a Git commit, container image digest, full dependency/environment identity,
signature, or proof the original research ran that build. Offline replay uses the installed
implementation; it does not retrieve or switch to a historical build automatically.

## Publication and verified reads

Canonical package JSON is compact, key-sorted UTF-8, with explicit normalized domain serialization.
Its identity is `sha256:` followed by 64 lowercase hex characters. Publication layout is:

```text
<private-root>/<64-hex-digest>/evidence.json
<private-root>/<64-hex-digest>/manifest.json
```

The manifest has a fixed `evidence.json` payload name, fingerprint and bounded byte size. Payload
is written and synchronized first; the manifest is written last. Each write uses a same-directory
temporary file, content `fsync`, atomic replacement and directory `fsync`. This supplies ordered
manifest-last publication on filesystems honoring those operations, not a transactional filesystem
or storage authorization. An interrupted orphan directory is not reusable evidence. Re-export of
identical content reuses a verified publication; it does not overwrite an existing package.

Reads verify schema, canonical directory name, fixed payload containment, supported file/size,
size/hash equality and canonical bytes before returning the materialized package. Modified payloads
and copied manifests under another digest name are refused. Individual artifacts are bounded at
16 MiB. Symlink checks and resolved containment are defenses within a private worker-owned root,
not a race-proof shared-host storage sandbox; keep untrusted writers out of that root. A party
able to replace both payload and manifest can manufacture a self-consistent digest. Integrity is
not authenticity or authorization.

## Independent outputs and comparison semantics

`run` executes the orchestration, then compares the resulting projection with each `expected`
projection. The projection covers cash, runtime phase, positions, orders and fills; it is not every
persistent database/journal field. In-memory replay does not reproduce PostgreSQL isolation,
leases, process-loss durability, the complete worker scheduler or venue microstructure.

Reports keep these questions separate:

1. **`execution`**: did the actual isolated execution/reconciliation path reproduce the captured
   projection using the supplied broker/runtime inputs? `match` applies only to that boundary.
2. **`same_config_research_execution`**: do the research signal evaluator and execution entry
   evaluator agree on entry-condition outcomes and indicator values for the supplied deployed
   configuration? This is an entry-signal/indicator comparison, **not full economics parity**.
   It does not run the backtest fill model against live outcomes, compare exit economics, or
   certify costs, capital, PnL, partial fills or latency models.
3. **`original_research_deployed`**, with **`configuration_differences`**: are there financial or
   execution-rule edits between supplied original and deployed strategy documents? Identity and
   annotations (`strategy_id`, name, description, creation time, metadata) are aligned for this
   diff; the financial rules remain compared. Differences prove mismatch. No differences still
   yield unavailable original capital/cost/build evidence, not parity.
4. **`ledger`**: independent Decimal64 cash/quantity folding, alongside fee/realized calculations
   from the existing ledger kernel. Fill side comes from normalized order evidence. No mark,
   future exit fee, account equity, risk-capital or allocation-normalized return is invented.
   A cashflow match is not signal or execution parity.
5. **`acceptance`**: with `--expected`, compare recomputed report data with an external
   schema-valid golden, excluding the acceptance result itself. The golden is not engine input.
   A match can accept a report that explicitly contains missing evidence or a known mismatch;
   automation must inspect the report as well as the exit code.

6. **`boundaries`**, with each comparison's **`scope`**: `indicators_signals` carries the
   entry-signal/indicator result; `sizing_risk`, `orders_fills` and `accounting` classify
   independently what the supplied evidence supports. An isolated expected-outcome match is
   not a captured decision trace. Prospective `DYNAMIC_RISK_READS_NOT_CAPTURED` keeps sizing/risk
   unavailable; otherwise that seam still discloses `RISK_DECISION_TRACE_NOT_CAPTURED`.
   Accounting uses `recorded_fill_cashflow_only` when ledger evidence exists, otherwise the
   isolated recorded-outcome scope. Do not promote one seam's match into another's evidence.
7. **`build_compatibility`**: compares captured execution Python-source identity with installed
   Python source. Equality is `match`; different or missing evidence is `unavailable` with
   `CAPTURED_BUILD_NOT_VERIFIED_AGAINST_CURRENT`. Neither result establishes original research
   build compatibility, dependency identity, source authenticity or authority. The report
   limitation `CURRENT_BUILD_DIFFERENTIAL_IS_NOT_ORIGINAL_BUILD_PARITY` remains explicit.

A historical ARB-USDC `2h` ledger/config investigation with missing as-seen candles, broker sequence
or original research/build facts cannot be labeled full historical parity. Later downloads and
new prospective packages cannot repair that absence retroactively. Public round-number synthetic
fixtures exercise behavior; they are not copies of an operator's account history.

## Timing evidence

The closed-bar observer records `data_available_at` when entering the wrapper, not the original
provider ingestion timestamp; then `evaluation_started_at` and `decision_completed_at` around the
orchestration. Monotonic elapsed and process CPU clocks stop before evidence construction,
source hashing and file publication. Process CPU is not exclusive task CPU and may include other
threads in the worker process.

Existing ADR 0131 database/venue ledgers supply call-count and duration deltas when bound. Missing
instrumentation produces `null` plus unavailable reasons. Broker event start/completion timestamps
span normalized method calls, not precise network wire send/ack instants. Queue wait is not
measured. These gaps remain explicit (`QUEUE_BOUNDARY_UNAVAILABLE`, `WIRE_SEND_ACK_UNAVAILABLE`,
`CPU_PROCESS_SCOPE_NOT_EXCLUSIVE`, and relevant instrumentation reasons). The offline runner does
not sleep according to recorded latency; timings identify the observation, not simulated queue
position or a comparable throughput benchmark. Existing operator `runtime` cycle telemetry remains
the supported whole-cycle diagnostic; replay does not replace it.

## Failure and acceptance contract

| Exit | Meaning |
| --- | --- |
| `0` | Successful help/schema/export/inspect, or `run --expected` matching the external golden; never a blanket parity certificate |
| `1` | A computed comparison mismatch, or an external golden mismatch |
| `2` | Rejected/unavailable artifact, schema/semantic/integrity failure or unusable input tape; stderr is a redacted rejection |
| `3` | A produced report lacks required parity evidence, including otherwise successful synthetic execution without original research |

Ordinary `run` reports mismatch first; otherwise the current bounded contract returns `3`, not a
full-parity success. `--expected` makes exit status the external acceptance result, while all
comparison statuses/limitations remain in JSON. Invalid CLI syntax also uses argparse's usage
failure; read stderr rather than assuming every exit `2` is a missing file.

`inspect` is not execution: it reports fingerprint, kind, declared gaps and ledger fill count and
keeps `execution_completeness: unavailable`. `run` supplies the recomputed detail. Neither
verification nor a successful golden authorizes deployment, source/build trust or economic edge.

## Prospective capture disposition

Worker capture is disabled unless `THYTRADER_REPLAY_CAPTURE_ROOT` is nonempty at execution-worker
startup. It applies to live closed-bar processing, not paper broker wrapping or every initial
reconciliation. Defaults are 256 published packages and a 64 MiB accounted JSON budget; there is
no automatic pruning or CLI budget/retention setting. Accounting is a publication guard, not a
whole-volume quota (manifest/temp/orphan overhead and concurrent writers are outside that sum).
Clock and broker buffers are capped at 5,000 events each. A full budget refuses new capture rather
than altering old evidence.

Capture returns the completed trading result on buffer exhaustion or caught evidence-publication
failure, increments an in-process failure counter, and emits a redacted capture-failure log.
There is currently no capture-status operator endpoint, durable capture failure metric or alert;
operator cycle reports do not certify capture health. Runtime exceptions/cancellation from the
underlying trading call retain their ordinary behavior and do not imply a completed package.
The publication await has a two-second timeout, but filesystem work runs in a thread and can
finish after that timeout; reconcile the artifact before retrying. This is not a hard bound on
source hashing, package assembly or total cycle overhead, and it does not roll back trading.

Root choice, permissions, opt-in/disable, retention and readback requirements are in the
[operator reference](../../skills/thytrader-runtime/references/live-execution-replay.md).
