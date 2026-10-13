# 0137: Immutable as-seen inputs and offline live-execution replay

- Status: Accepted
- Date: 2026-10-13
- Relates to: [0030](0030-agent-e2e-primary-surface.md),
  [0082](0082-strategy-root-mutable-strategies-auto-snapshots.md),
  [0083](0083-unified-backtest-model.md),
  [0097](0097-runtime-parity-and-observability.md),
  [0116](0116-live-execution-evidence-and-backtest-bar-explanations.md),
  [0131](0131-execution-cycle-timing.md),
  [0132](0132-execution-cycle-shared-reads-and-budget.md)
- Contract: [Offline execution replay](../architecture/live-execution-replay.md)
- Amends in part: [0030](0030-agent-e2e-primary-surface.md), only for the file-only offline
  evidence companion: CLI, portable schemas and skills are its supported surface; existing
  HTTP/browser trading-lane completeness and confirmation requirements remain unchanged.
- Operator procedure: [Replay reference](../../skills/thytrader-runtime/references/live-execution-replay.md)

## Context

A recorded fill ledger can reproduce cash movements without reproducing the decisions that
created them. Likewise, a backtest on today's historical candles does not recreate the mutable
candle windows, account state, quotes, or acknowledgements a worker actually saw. Comparing a
research strategy with a deployed edit also confounds configuration changes with execution
semantics. A focused ARB-USDC `2h` investigation therefore needs explicit evidence boundaries,
not an assertion of historical parity from fills or matching strategy names.

Existing decision journals, execution-quality reports and cycle telemetry remain useful, but
are not complete replay inputs. Exact historical as-seen candles, the broker observation sequence,
and original research/build evidence cannot be reconstructed merely by enabling capture now.

## Decision

Add an agent-first, offline `thytrader-replay` CLI and an opt-in prospective observer at the
actual live worker's closed-bar boundary. No browser UI or HTTP replay endpoint is introduced.
The following requirements are normative; field shapes and procedures live in the linked contract
and operator reference.

1. **Publish immutable inputs, not a narrative.** A versioned package binds the exact strategy,
   initial book, completed candle windows, product precision, risk/portfolio/mark/fee inputs,
   runtime clock/UUID tape, normalized broker request/response sequence, expected ending state,
   timing observations and known source identities. Missing evidence MUST remain absent with
   reasons. Captured inputs MUST NOT be replaced by later market downloads or inferred fills.
2. **Reverify before consumption.** Canonical evidence bytes determine a SHA-256 identity.
   Publish payload before manifest at its content-addressed location; reuse an existing identity
   only after verified equal-content readback. Reject malformed, noncanonical, oversized,
   misplaced or tampered artifacts. An interrupted directory without a valid manifest is not
   published evidence. Ordered file/directory synchronization relies on filesystem `fsync`
   semantics; it is not a multi-file transaction or tamper-proof storage.
3. **Execute rather than echo.** Replay MUST invoke the existing closed-bar or order-reconciliation
   orchestrator in memory with scripted broker inputs. It MUST independently compare resulting
   cash, phase, positions, orders and fills with captured expectations. Unexpected requests,
   changed causal order or unconsumed input tape MUST fail closed. Expectations are not engine
   inputs. Ledger arithmetic is a separate cashflow reconstruction, not execution proof.
4. **Separate comparisons.** Same-config research versus execution compares entry-signal and
   indicator evaluation on the supplied windows; it MUST NOT be presented as full backtest versus
   live economics parity. Original research versus deployed rules is a separate configuration
   diff. Financial-rule changes can establish mismatch; equal rules cannot certify original
   capital, costs, dataset, source build or economic results. Unknown original source identity
   MUST NOT be filled from the current checkout.
5. **Measure named boundaries.** Disclose data-available, evaluation-start and decision-complete
   observations, elapsed time, process CPU and already-instrumented database/venue call deltas.
   Broker method spans MUST NOT be labeled actual wire send/ack latency. Queue wait and missing
   instrumentation remain unknown, never zero. Offline speed is not a production latency claim.
6. **Keep authority absent.** The offline CLI MUST NOT construct application settings, a real
   broker, network client or database connection, and MUST NOT send, cancel, arm, resume or
   deploy orders. `export` mutates local evidence files only. Artifact publication authorization
   is distinct from trading authority and does not inherit or grant YOLO/live consent.
7. **Capture prospectively and privately.** Capture is off by default, enabled only by an explicit
   execution-worker root, and bounded in memory and publication size/count. Capture MUST NOT
   repeat a broker operation on failure. Failed capture is observable but does not roll back
   completed trading or substitute invented evidence. Publication can add overhead; the observer
   is not a zero-latency guarantee. Disable at worker startup, retain prior packages unchanged,
   and never imply past windows have been recovered.
8. **Make acceptance portable.** Public JSON schemas, normalized fixture inputs, golden reports,
   stable refusal codes and deterministic comparisons form the acceptance boundary. Future
   language implementations need not import private Python modules. Matching an external golden
   establishes that bounded acceptance case, not historical parity, provenance authorization or
   economic edge.

## Consequences

- Agents can export, inspect and run packages without a running ThyTrader installation or venue
  credentials. Runtime, research and operator skills discover the same offline surface without
  collapsing their trading-authority lanes.
- Private account inputs and output reports stay outside public source/fixtures. Public fixtures
  are independently synthetic, never lightly anonymized operational records.
- Prospective capture starts after ordinary pre-bar reconciliation and preparation; it does not
  capture that preceding stage or all dynamic risk reads. A successful replay of its recorded
  boundary cannot certify a complete worker cycle or database crash/restart behavior.
- Missing evidence is a valid reported outcome. Successful synthetic execution still lacks
  original research evidence; an external golden may pass while the report retains that absence.
- No automatic retention pruning, backfill, strategy promotion, source rewrite, migration or live
  activation is authorized by this decision.

## Alternatives considered

- Replay fills alone: useful for arithmetic, insufficient for decision or order-lifecycle parity.
- Download historical candles at replay time: not the original as-seen input and not offline.
- Treat one evaluator or equal strategy fingerprints as full parity: hides capital, costs,
  execution, input and build differences.
- Require private Python imports for acceptance: makes a future implementation reproduce source
  layout rather than the observable data contract.
- Capture every fleet cycle by default: expands private data retention and overhead without
  operator opt-in; a bounded closed-bar slice is the chosen boundary.
