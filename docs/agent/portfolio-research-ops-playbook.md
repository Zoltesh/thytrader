# Agent playbook: portfolio visibility then research

Contributor-facing recipe for operating agents. Uses **existing** CLIs and HTTP routes only.
Open [`ops/`](../../ops/README.md) when driving a running instance; run every
`uv run thytrader-*` command from the repository root.

This path answers: *"What do I hold, is the stack healthy, is data complete, and can I backtest a
published strategy?"* It does **not** deploy paper or live unless the operator explicitly asks for
that later step ([`skills/thytrader-playbook/SKILL.md`](../../skills/thytrader-playbook/SKILL.md) or
[`skills/thytrader-runtime/SKILL.md`](../../skills/thytrader-runtime/SKILL.md)).

## Three portfolio surfaces (do not conflate)

| Surface | What it shows | How an agent reads it |
| --- | --- | --- |
| **Account portfolio** | Demo or Coinbase balances and portfolio history for the UI | `GET /api/v1/portfolio`, `GET /api/v1/portfolio/history?range=7d\|24h\|30d\|forever` — **no** `thytrader-operator` subcommand today |
| **Deployment inventory** | Quantities, orders, fills, capital, protection per book | `uv run thytrader-runtime show DEPLOYMENT_ID` or `GET /api/v1/deployments/{id}` |
| **Diagnostic inventory** | Redacted multi-book phase/side/protection without sizes | `uv run thytrader-operator strategies` / `runtime` (`books[]`) |

Operator `health` may report a `portfolio_history` **component** (freshness of history snapshots).
That is not the same as account balances. Operator `risk` and `monitor` set `balances_omitted: true`
by design.

After [ADR 0060](../../decisions/0060-multi-book-deployment-api.md): never infer a secondary open
book from the deployment primary `product_id`. Read `positions[]` and `book_totals` on runtime
`show`; treat the singular HTTP `position` field as compatibility-only.

## Numbered recipe

### 0. Preflight

1. `uv run thytrader-operator health` — stop on `failed`; treat `degraded` as incomplete evidence.
2. If the CLI exits on ops-contract or version mismatch, ask to rebuild with `make run` from the
   repository root. Package version `0.1.0` alone is not current-image proof.
3. `uv run thytrader-operator configuration` — note `yaml_source_of_truth`, YOLO tiers, and
   `settings_file`. YOLO applies without restart ([ADR 0055](../decisions/0055-yaml-settings-runtime-reloadable-yolo.md)).
   Leftover `THYTRADER_YOLO_TIERS=paper` is valid plain env, not JSON.

### 1. Portfolio snapshot (read-only)

4. `GET /api/v1/portfolio` on loopback — current balances (demo when Coinbase credentials are empty).
5. Optional: `GET /api/v1/portfolio/history?range=7d` — wall-clock history for context (gaps stay
   visible; no Y interpolation).
6. If deployments exist: `uv run thytrader-operator runtime` for redacted `books[]`, then
   `uv run thytrader-runtime show UUID` when quantities, orders, fills, or capital are needed.

### 2. Data health for the research clock

7. `uv run thytrader-operator data-catalog` — judge configured coverage by **`watch_complete`**, not
   island `complete` alone.
8. `uv run thytrader-operator products` — confirm the product is enabled.
9. If `watch_complete` is false: `uv run thytrader-data inspect-gaps --product-id … --timeframe …`.
   If that report sets `truncated`, treat `gap_summary` as partial. Wait for the worker to
   self-complete lookback; `fill-gaps --confirm` only after a durable hole per
   [`skills/thytrader-data/SKILL.md`](../../skills/thytrader-data/SKILL.md). Never interpolate.

**Extra clocks:** HTF filters and per-indicator timeframes need ingest on **each** referenced clock
before backtest or deploy. The bundled `thytrader-playbook run` watches only its `--timeframe`
decision clock — ingest extras explicitly with `thytrader-data`.

### 3. Research (draft → publish → backtest)

10. Prefer **`thytrader-bar-backtest-v4`** for new bar backtests (default in
    [`skills/thytrader-research/SKILL.md`](../../skills/thytrader-research/SKILL.md)). v1/v2-only
    images return 422 on v3/v4 requests.
11. Build `request.json` for `submit-backtest`:
    - Copy `dataset_fingerprint` (and `htf_filter.dataset_fingerprint` / `indicator_dataset_fingerprints`
      / `additional_instrument_datasets` when present) from `data-catalog` rows — do not invent windows.
    - Optional fee prefill: `GET /api/v1/fees` when credentials exist; demo mode leaves fees blank.
12. Draft path:
    - Quick template: `uv run thytrader-research create-draft --template rsi-mean-reversion --confirm`
    - Full JSON (HTF, multi-instrument, per-indicator TF): `save-draft --file draft.json --confirm`
      — `create-draft` alone does not emit those fields.
13. `uv run thytrader-research publish --strategy-id UUID --confirm`
14. `uv run thytrader-research submit-backtest --file request.json --confirm`
15. Read `validity_limits` on v4 summaries before claiming paper/live parity
    ([ADR 0062](../decisions/0062-research-paper-semantics-audit-stage-4.md)).

Composed studies (OOS, walk-forward, cross-market, sweep, WFO) stay in the research skill; this
playbook does not sequence them automatically.

### 4. Correlate results (read-only)

16. `uv run thytrader-operator performance --result-fingerprint sha256:…` or `--deployment-id UUID`
17. `uv run thytrader-operator studies` — persisted catalog rows only (no child equity).
18. Stop before deploy unless the operator asked for paper/live — use `thytrader-runtime` or
    `thytrader-playbook run --paper-cash …` with explicit `--confirm`.

## Bundled shortcut

When the operator wants one decision clock only and no portfolio prelude:

```bash
uv run thytrader-playbook run --product-id BTC-USD --timeframe 1h --ingest --create-draft --publish --backtest-file request.json --confirm
```

See [`skills/thytrader-playbook/SKILL.md`](../../skills/thytrader-playbook/SKILL.md) for flags and
YOLO rules. The playbook never starts live.

## Lane boundaries (unchanged)

- Operator stays read-only.
- Data / research mutations need `--confirm` unless YOLO covers that tier.
- Runtime and live need separate authority; this playbook does not grant it.
- Memory mutations always need `--confirm`; YOLO never covers memory.

## Related docs

- [`docs/agent-integration.md`](../agent-integration.md) — safety model and skill packaging
- [`docs/user/operate.md`](../user/operate.md) — human browser-first workflow
- [`skills/README.md`](../../skills/README.md) — canonical skill index
