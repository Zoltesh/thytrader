# Immutable Research-Run Specification

## Purpose

A research-run specification records exactly which immutable strategy and candle dataset a future
simulation is allowed to consume, together with the deterministic request assumptions that must not
be inherited from ambient configuration. It is an immutable research artifact, not evidence that a
backtest ran and not authority to place an order.

Research-run-spec publication remains an internal application boundary. The browser strategy API now
authors revision-guarded drafts, publishes immutable strategy evidence, and submits deterministic
backtests whose server-side workflow derives and persists the eligible run specification and immutable
result. A read-only CLI can also evaluate an existing publication that selects the explicit signal-
engine contract. There is still no paper broker, Coinbase order call, or live-trading path.

## Canonical V1 document

Every document is a frozen, unknown-field-rejecting Pydantic model. Canonical JSON uses sorted object
keys, compact separators, UTF-8, canonical UTC timestamps with a `Z` suffix, and exact lexical decimal
normalization. Floats and exponent notation are rejected. The full canonical document is addressed by
`sha256:<64 lowercase hexadecimal characters>`.

| Field | Implemented contract |
|---|---|
| `schema_version` | Exact literal `1.0`. |
| `run_id` | UUIDv7 whose embedded Unix millisecond matches `created_at`. It identifies one immutable research request. |
| `created_at` | Timezone-aware UTC creation instant. |
| `strategy_fingerprint` | Exact published canonical strategy fingerprint. |
| `dataset_fingerprint` | Exact verified immutable dataset fingerprint. |
| `evaluation` | Non-empty, whole-hour UTC, half-open `[starts_at, ends_at)` interval. |
| `warmup` | `bars` plus the exact derived `starts_at`; its interval is `[starts_at, evaluation.starts_at)`. |
| `capital` | USD-only initial quote balance, greater than zero and at most `1e18`. |
| `costs` | Maker/taker fee rates from zero through `0.1`, maker no greater than taker, and fixed slippage from zero through `1000` basis points. |
| `broker` | Omitted for legacy contracts. `thytrader-bar-backtest-v2` requires one fully resolved constant-spread broker block: zero through `1000` total spread basis points, full fills, bid-side triggers, and bid-close marking. |
| `bar_execution` | Signals use completed candle closes; modeled fills use the next candle open. |
| `engine_contract_version` | `thytrader-bar-v1` remains request-only; `thytrader-bar-signal-v1` selects deterministic signal evaluation; `thytrader-bar-backtest-v1` and `thytrader-bar-backtest-v2` select their separately documented simulation semantics. |
| `random_seed` | Explicit integer from zero through signed 64-bit maximum. |

Equivalent accepted decimal spellings such as `10000.00` and `10000` share canonical identity. Digits
beyond Python's ambient decimal precision are preserved lexically and remain fingerprint-significant.
The UUID, creation time, authored intervals, assumptions, seed, and both artifact fingerprints are all
identity-bearing.

## Interval and coverage semantics

Dataset manifests describe complete hourly candle coverage as a half-open interval
`[manifest.starts_at, manifest.ends_at)`. A research request is eligible only when:

1. `warmup.starts_at == evaluation.starts_at - warmup.bars * 1h`;
2. `warmup.bars` exactly equals the published strategy's declared `data_requirements.warmup_bars`;
3. the verified dataset begins no later than `warmup.starts_at`;
4. the evaluation interval contains at least one hourly candle; and
5. the verified dataset ends no earlier than `evaluation.ends_at + 1h`.

The final extra candle is required because a signal evaluated at the close of the final eligible candle
may only use the next candle's open as a modeled fill price. It is fill lookahead data, never signal
lookahead data.

## Publication and loading

PostgreSQL table `published_research_run_specs` stores the canonical document and denormalized identity
facts. Publication proceeds fail closed:

1. serialize and round-trip validate the supplied typed model before artifact or insert access;
2. load and cryptographically reverify the exact published strategy;
3. load and reverify the exact immutable dataset manifest and Parquet content;
4. require the existing immutable strategy/dataset binding;
5. validate strategy, provider, product, timeframe, warmup, interval, and fill-lookahead compatibility;
6. treat conflicts on either fingerprint or run identity as no-op candidates, then reject a reused
   `run_id` with different content after the requested fingerprint fails to reload; and
7. reload canonical content and reverify every denormalized row identity.

Every load repeats canonical-byte, fingerprint, row-identity, strategy, binding, dataset, and eligibility
verification. A database row is therefore a publication record, not proof that missing or corrupted
external artifacts remain consumable. PostgreSQL remains the publication/coordination authority;
immutable manifests and Parquet remain the candle-content authority.

The market-data worker owns the dataset root privately. Operators must not mutate manifests or Parquet
files in place, and no untrusted process may write there. Publication and loading intentionally reverify
artifacts immediately before use, while the composite PostgreSQL foreign key prevents the exact binding
from disappearing after a run specification is inserted; filesystem and PostgreSQL transactions cannot
be made atomic, so private single-writer ownership is part of the supported local threat model.

## Deliberate boundary

`thytrader-bar-v1` permanently identifies only the request contract and its completed-close/next-open
timing convention. Existing publications using it are not executable. The identity-bearing
`thytrader-bar-signal-v1` value selects the formulas and entry-condition semantics in
[signal-evaluation.md](signal-evaluation.md).

`thytrader-bar-backtest-v2` selects the same signal stage plus the spread-aware simulator described in [backtest-simulation.md](backtest-simulation.md). It requires a canonical `broker` block, so a command-line spread value can never be ambient configuration or omitted from run identity. The constant spread is a disclosed stress assumption, not observed historical bid/ask evidence.

Signal evaluation still does not define latency, partial fills, rejection policy, cooldown or
position state, SL/TP ordering, PnL, metrics, or result persistence. A deterministic condition trace is
therefore not an executed backtest or result.
