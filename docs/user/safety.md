# Safety

ThyTrader can place irreversible financial orders. Safety is product behavior, not an optional
theme.

## Local-first

- Application services bind to `127.0.0.1` by default.
- Clone-and-run host ports are loopback only (dashboard `5175`, API `8200`, PostgreSQL `5439`).
- Remote exposure is never the silent default. Do not weaken startup safety to make LAN access
  convenient.
- A loopback-only first install does not require a login.

## Secrets

- Coinbase keys stay server-side. Never put them in the browser, logs, exceptions, support bundles,
  agent output, or Git.
- An in-app operator-chat LLM key is also server-side (API process only) and is **not** a Coinbase
  credential. Status never echoes it. Do not paste a Coinbase key into `/chat`.
- `.env` is ignored. `.env.example` contains names and placeholders only.
- `make run` / `scripts/setup_local_stack.py` must not print credentials or connection URLs.
- View + Trade is enough for planned trading. Extra key permissions are accepted and reported; they
  are not consent to skip arming, risk, or confirmation.

## Confirmation and live arming

- Read-only observation (`thytrader-operator`) never places, edits, or cancels orders and never arms
  live trading.
- Mutations use separate tools and require `--confirm` unless you explicitly enable YOLO on an
  allowed tier. YOLO is **off** by default. Toggle it from `/settings` or `thytrader.yaml`
  ([ADR 0055](../decisions/0055-yaml-settings-runtime-reloadable-yolo.md)); leftover
  `THYTRADER_YOLO_TIERS=paper` is valid.
- Live start (and live on-demand place-order) also require `--i-understand-live`. YOLO never skips
  that flag. Live place-order, risk-policy publication, `--local` research, and memory stay
  confirmation-hard-gated even when YOLO advertises `live`.
- The playbook never starts live. Memory mutations never inherit YOLO.

## Skill lanes

Operator, data, research, runtime, playbook, and memory stay separate. Observing health is not
permission to trade. Research is not permission to deploy. See [Operate](operate.md) and
[`skills/README.md`](../../skills/README.md).

## Trading invariants (short)

- A signal or discretionary action creates an **order intent**. It does not call Coinbase around
  risk checks.
- Persist intent before submission. A network timeout is ambiguous — reconcile before retrying.
- Resume after restart only after reconciling balances, open orders, fills, and local state.
- Block new risk-increasing orders on stale data or unhealthy required connections.
- Missing candles are never interpolated.

The full baseline (risk-policy registry, execution policy, audit) is
[security and trading-risk](../security-and-risk.md).
