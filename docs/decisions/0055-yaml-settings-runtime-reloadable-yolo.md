# 0055: YAML non-secret settings and runtime-reloadable YOLO

- Status: Accepted
- Date: 2026-09-16
- Relates to: [0030](0030-agent-e2e-primary-surface.md),
  [0034](0034-phase-12-agent-orchestration-yolo.md),
  [0043](0043-yolo-live-skip-confirm.md),
  [0051](0051-in-app-operator-chat.md),
  [0052](0052-richer-sweep-axes-study-catalog.md),
  [0054](0054-trade-reason-journals.md)

## Context

YOLO on/off and the independent `data` / `research` / `paper` / `live` tier set lived in
`.env` as `THYTRADER_YOLO_ENABLED` and `THYTRADER_YOLO_TIERS`. pydantic-settings JSON-decodes
complex types, so leftover `THYTRADER_YOLO_TIERS=paper` (and comma lists or an empty value)
raised `SettingsError` at process start. Only JSON `["paper"]` worked. Compose injected
`THYTRADER_YOLO_TIERS=` which hit the same decoder.

Operators need YAML as the source of truth for **non-secrets**, including YOLO, with apply
without restarting API or workers. Secrets (Coinbase keys, database URL, notify webhook URL,
LLM keys) stay in ignored `.env`. Bind address, port, environment, and dataset root stay
env-at-boot. Live still requires `--i-understand-live`. The playbook never starts live.
Extra exchanges stay out.

[ADR 0053](0053-workstation-ia-write-only-coinbase-credentials.md) (sibling PR #71) owns
workstation IA and the write-only Coinbase secrets form. [ADR 0054](0054-trade-reason-journals.md)
(PR #72, now on `main`) owns why-trade journals, ops `v20`, and Alembic `0032`. This slice is
ADR **0055**. It ships `/settings` with the YAML panel when 0053 is not yet merged. If 0053
lands first, fill its YAML/YOLO placeholder rather than forking a second Settings shell.

## Decision

1. **YAML file** `thytrader.yaml` (override `THYTRADER_SETTINGS_FILE`) holds non-secret knobs:
   `yolo.enabled`, `yolo.tiers` (scalar `paper` or a list), `log_level`, snapshot / market-data /
   execution intervals, market-data lookback and default product, `notify_provider`.
2. **YAML wins leftover env** via Settings kwargs. `yolo_tiers` is `Annotated[..., NoDecode]`
   plus `parse_yolo_tiers_value` so `THYTRADER_YOLO_TIERS=paper` is valid leftover.
3. **Secrets stay out.** YAML that names credential-shaped keys is rejected. GET/PUT
   `/api/v1/settings` never echoes secrets.
4. **Apply without restart.** API `RuntimeState.settings` re-reads YAML mtime. Workers re-read
   intervals (and ingest product/lookback) each cycle. `notify_provider` reloads. Bind, dataset
   root, Coinbase keys, database URL, and webhook URL still need a restart.
5. **Loopback Settings page** (`/settings`) exposes the YOLO toggle, independent tier enum, and
   the moved knobs. Coinbase secrets are not on this form.
6. **Hard gates unchanged.** `--i-understand-live` is never skipped. Live place-order,
   `set-risk-policy`, `set-settings`, `--local` research, and memory stay `--confirm`. Playbook
   never inherits live. YOLO tiers remain an independent unique subset ([ADR 0034](0034-phase-12-agent-orchestration-yolo.md),
   [ADR 0043](0043-yolo-live-skip-confirm.md)), not a hierarchy.
7. No ops-contract or Alembic bump (remain `thytrader-ops-contract-v20` / `0032` from ADR 0054).
   No extra exchanges.

## Consequences

- Paper-tier YOLO no longer depends on JSON env encoding.
- Operators change YOLO from the Settings page or `thytrader-runtime set-settings --confirm`
  without recycling Compose processes.
- Leftover `.env` YOLO keys remain parsed when YAML omits them; committed Compose no longer
  injects empty `THYTRADER_YOLO_TIERS`.
- Coinbase credential rotation remains the 0053 secrets surface.
- Why-trade journals remain ADR 0054.
