# Offline live-execution replay operator reference

Use `thytrader-replay` to inspect or reproduce a supplied private/synthetic evidence package.
This is a file-only companion to the operator, research and runtime lanes, **not** a new trading
lane. Run commands from the repository root (parent of `ops/`). No running API, database,
credentials, `.env` read or network broker is required by the replay CLI. It never sends orders,
changes a live book, arms, resumes, deploys or cancels. Do not edit source to make a replay pass.

## Discover and invoke

```bash
uv run thytrader-replay --help
uv run thytrader-replay export --help
uv run thytrader-replay inspect --help
uv run thytrader-replay run --help
uv run thytrader-replay schema --help
```

| Task | Command |
| --- | --- |
| Publish normalized input JSON | `uv run thytrader-replay export --input ./private-replays/input.json --root ./private-replays/packages` |
| Reverify publication and declared gaps | `uv run thytrader-replay inspect "$MANIFEST"` |
| Recompute and retain machine-readable output | `uv run thytrader-replay run "$MANIFEST" --format json` |
| Human-readable comparison | `uv run thytrader-replay run "$MANIFEST" --format text` |
| External golden acceptance | `uv run thytrader-replay run "$MANIFEST" --expected ./private-replays/expected.json --format json` |
| Discover portable wire shapes | `uv run thytrader-replay schema --kind package` (also `manifest`, `report`) |

`MANIFEST` is the exact `manifest` path returned by `export`, not a guessed filename. Keep the
returned `fingerprint`. It identifies canonical package bytes at
`<root>/<64-hex-digest>/manifest.json` plus `evidence.json`. `export` accepts normalized package
JSON, not a support bundle, database dump, research request or arbitrary raw venue response.
The exported schema is the supported input contract; do not construct private Python objects.
`inspect` prints JSON only; it is integrity verification, not a run or completeness certificate.

### Credential-free synthetic example

This writes local fixture publications only. `./private-replays` is an illustrative directory;
choose a private location **outside the checkout** for real evidence, since this name is not a
promise of Git ignore coverage. Authorize the chosen root before publication.

```bash
umask 077
mkdir -p ./private-replays
uv run thytrader-replay export \
  --input tests/replay/fixtures/no-signal.input.json \
  --root ./private-replays/packages > ./private-replays/export.json
MANIFEST=$(python -c 'import json; print(json.load(open("./private-replays/export.json"))["manifest"])')
uv run thytrader-replay inspect "$MANIFEST"
uv run thytrader-replay run "$MANIFEST" --format json > ./private-replays/report.json
REPLAY_EXIT=$?
printf 'Replay exit: %s\n' "$REPLAY_EXIT"
```

The synthetic example can match execution and entry-signal evaluation while returning `3` for
missing original research evidence. Under shell `set -e`, explicitly handle this expected status;
do not discard a valid JSON report solely because the exit is nonzero. Other independently
synthetic inputs live under `tests/replay/fixtures/`. They are acceptance cases, not live history.
A golden must be independently established for the intended acceptance case; copying the output
of the implementation being tested and calling that independent proof is circular.

## Read the report before drawing conclusions

- `execution`: recomputed closed-bar/reconciliation cash, phase, positions, orders and fills
  versus package expectations. The runner uses the actual orchestration and a scripted broker
  in memory; it is not a real order or database durability test. Each step starts from its own
  supplied `before`, not the previous step's result.
- `same_config_research_execution`: research signal evaluator versus execution entry evaluator,
  comparing entry outcomes and indicator values. A `match` is **entry-signal/indicator agreement
  only**, not full research-versus-live economics parity, exit parity or profitability.
- `original_research_deployed` / `configuration_differences`: original rules versus deployed
  rules, with annotations/identity aligned. A financial-rule edit can prove mismatch. No edit
  cannot prove original costs, capital, dataset or build match. Missing original input stays
  unavailable, never inferred from current strategy name or source checkout.
- `ledger`: exact observed-fill cashflow reconstruction; not a source of missing signals,
  venue fills, marks, account equity or original research results.
- `acceptance`: only present when `--expected` checks an external schema-valid report. Matching
  that golden can include expected unavailable/mismatch states; it is not a parity promotion.
- `boundaries` / comparison `scope`: distinguish `indicators_signals`, `sizing_risk`,
  `orders_fills` and `accounting`. An isolated ending-state match does not supply an unrecorded
  risk decision trace; prospective dynamic risk gaps keep sizing/risk unavailable. Accounting
  cashflow and isolated order outcomes have their own scope, not a universal parity flag.
- `build_compatibility`: captured execution Python-source identity versus installed source only.
  Missing/different source is unavailable, not permission to invent the original build. Matching
  is not dependency/environment identity or original research-build parity.
- `limitations` and reason lists remain binding even when another comparison matches. Recorded
  build identity is evidence, not authenticity, permission to trade or economic edge.

| Exit | Action |
| --- | --- |
| `0` | Help/schema/export/inspect succeeded, or external golden matched; still read comparison scope |
| `1` | Report mismatch; retain report and exact package/golden identities, do not retune expectations to hide it |
| `2` | Usage error or evidence rejected/unavailable; read redacted stderr, verify paths/schema/integrity/tapes, do not bypass validation |
| `3` | Report produced but parity evidence missing; retain and name missing evidence, do not report full parity |

An ordinary successful synthetic `run` currently returns `3`. A historical ARB-USDC `2h` case
without original as-seen candle windows, broker sequence or research/build evidence stays
incomplete. Newly downloading candles or enabling future capture does not recover the past.
Keep product, clock, quote currency and source identity explicit; do not substitute a USD proxy
for a USDC investigation.

### Semantic input refusals

Replay refuses conflicting product base/quote units, all duplicate mark product keys, broker
spans outside evaluation-start..decision-complete, and decreasing/out-of-envelope actual clock
reads. Historical book/order/fill timestamps and historical UUID timestamps remain allowed.
Equal observation instants are valid. Financial strings and typed amounts share the finite
64-digit coefficient envelope with adjusted exponent -128..128, stored exponent -191..128 and
196-character numeric lexeme budget; see the normative architecture/ADR for precise limits.
Cash remains signed. Unusable derived arithmetic exits `2` with redacted stderr rather than
silently becoming zero, saturating or printing a traceback. Ordinary rounding is still allowed.
Do not edit original evidence, change currency units or retune a golden to bypass a refusal.

## Prospective worker capture: opt-in, not a replay action

Capture observes the actual live worker's closed-bar call after ordinary pre-bar reconciliation
and preparation. One package contains one observed closed-bar step, with its original initial
book, supplied windows/risk/marks/fees, normalized broker sequence, runtime clock/UUID inputs and
ending projection. It does not capture the preceding reconciliation or every dynamic risk read.
Paper execution is not wrapped. A failure/cancelled trading call need not publish a package.

The only shipped startup opt-in is a nonempty execution-worker environment variable:

```text
THYTRADER_REPLAY_CAPTURE_ROOT=<approved-private-worker-visible-directory>
```

It is off by default. It is **not** a `thytrader-runtime set-settings` option, HTTP mutation,
YOLO tier, live-arm action or Compose capture command. The execution worker must receive the
variable and be able to write that directory; setting it on an unrelated shell/API process is
not proof capture is enabled. Changing service environment, mounts or restarting a worker
requires separate installation/restart authorization. Do not edit deployment files or restart a
running worker solely because an offline replay was requested.

To disable, remove/unset that variable (or set it empty) in the execution worker's startup
environment and apply the authorized worker restart. It is read at startup, not hot reloaded.
Disabling leaves previously published packages unchanged and does not pause/disarm trading.
This reference does not authorize changes to existing deployment intent.

### Privacy, budget and retention

- Private packages/reports contain account identifiers, balances, allocations, fills, policy and
  strategy details even without credentials. They are **not redacted support bundles**. Keep
  outside Git/public attachments; never paste account evidence into source, skills or ADRs.
- Use an operator-approved, worker-owned private root, restrictive parent permissions and
  `umask 077`. Newly created publication root/package directories request mode `0700` and
  temporary files are private. Existing directories/ancestors are not retroactively secured;
  inspect permissions before opting in. Do not use a shared/untrusted writable root.
- Defaults are 256 packages and a 64 MiB accounted JSON budget, plus 16 MiB per artifact and
  5,000 clock/UUID and 5,000 broker events per step. No CLI knobs for those capture budgets
  currently exist. The byte guard is not a filesystem quota; metadata/temp/orphan overhead
  and concurrent writers are not fully bounded by it.
- There is no automatic age pruning or rotation. Exhaustion rejects new captures instead of
  rewriting old evidence. Plan authorized private archival/retention before enabling, retain
  exact manifest/payload pairs and inspect archived packages at their digest-named location.
  Do not silently delete evidence to clear the budget or trade failures for false success.

### Capture failure and timing disposition

Publication/buffer failures keep the completed trading result, increment an in-process capture
failure count and emit a redacted `execution_replay_capture_failed` event; they do not repeat a
broker operation or roll back trading. There is no supported capture-status HTTP/CLI, durable
failure counter or capture alert yet. Operator agents must not scrape logs to invent one;
report that observability gap. An absent package is missing evidence, not proof the worker was
idle, healthy or disabled. Confirm any supplied publication with `inspect` before using it.

The publication await times out after two seconds, but its filesystem thread can still finish;
check the exact artifact before retrying. Source hashing/package preparation can add time
outside that timeout. Do not claim zero overhead, a hard whole-cycle bound or "trading unchanged"
as a production latency guarantee.

Timing distinguishes wrapper data-available, evaluation-start and decision-complete UTC instants;
monotonic elapsed/process CPU; and existing instrumented database/venue deltas when available.
Process CPU is not exclusive task CPU. Broker method start/completion is not actual wire send/ack.
Queue wait and absent instrumentation stay unknown, not zero. Offline replay does not wait out
recorded timings. Use `uv run thytrader-operator runtime --format json` for existing whole-cycle
telemetry, not a prospective capture-health certificate.

Contract detail: [architecture](../../../docs/architecture/live-execution-replay.md).
Decision: [ADR 0137](../../../docs/decisions/0137-offline-live-execution-replay.md).
