# Research comparison with offline execution replay

The file-only companion is `thytrader-replay`, not `thytrader-research --local` or an HTTP
research submission. It never changes a strategy, publishes a research result, deploys a bot or
sends an order. Start with `uv run thytrader-replay --help` from the repository root.

```bash
uv run thytrader-replay schema --kind package
uv run thytrader-replay inspect "$MANIFEST"
uv run thytrader-replay run "$MANIFEST" --format json
uv run thytrader-replay run "$MANIFEST" --expected ./private-replays/expected.json --format json
```

For authorized local artifact publication, use
`uv run thytrader-replay export --input FILE --root DIR`; use the exact returned `manifest`
path as `MANIFEST`. The [canonical replay procedure](../../thytrader-runtime/references/live-execution-replay.md)
has a credential-free fixture example, all commands/exit codes, privacy, retention, and the
separate prospective worker capture opt-in. Real account packages/reports belong outside Git.
No private Python imports, direct database reads or new venue downloads are acceptance inputs.

Keep three findings distinct:

1. `same_config_research_execution` compares entry-signal outcomes and indicator values on the
   supplied deployed configuration; it is not full backtest/live economics or exit parity.
2. `execution` independently replays the existing closed-bar/reconciliation boundary in memory
   with captured broker/clock/UUID inputs. The ending-state expectation is compared, not reused
   as the calculated outcome. It does not certify the complete worker cycle or database recovery.
3. `original_research_deployed` and `configuration_differences` compare supplied original/deployed
   rules, ignoring identity/annotations. Financial-rule differences establish mismatch; equal
   rules cannot establish the missing original capital, costs, dataset, build or research results.

`boundaries` and each comparison's `scope` separately classify indicators/signals, sizing/risk,
orders/fills and accounting. Missing dynamic risk evidence is not filled by an ending-book match.
`build_compatibility` compares captured execution Python source with installed source only;
missing/different evidence is unavailable, and equality does not prove original research-build
or dependency/environment compatibility.

Without `--expected`, a matching synthetic run can return `3` because original research is
missing. `1` means mismatch; `2` means rejected/unavailable artifact (or CLI usage failure).
With `--expected`, `0` means the external golden report matches and `1` means it differs, even
when the accepted report retains unknown evidence or a known bounded mismatch. Always read the
report statuses and limitations; a golden is acceptance data, not provenance or trading consent.
Do not derive an "independent" golden from the very implementation it is meant to accept.

A historical ARB-USDC `2h` replay lacking exact original candles, broker observation sequence
and research/build evidence remains incomplete. New prospective capture cannot backfill what
the original worker saw. Preserve the original clock/quote/configuration; do not optimize away
a discrepancy or label a later historical candle download as the original input.

Full contract: [architecture](../../../docs/architecture/live-execution-replay.md).
Decision: [ADR 0137](../../../docs/decisions/0137-offline-live-execution-replay.md).
