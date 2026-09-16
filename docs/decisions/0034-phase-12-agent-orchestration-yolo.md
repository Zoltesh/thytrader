# 0034: Phase 12 agent orchestration and YOLO confirmation opt-in

- Status: Accepted — superseded in part by [0043](0043-yolo-live-skip-confirm.md)
  (live `--confirm` hard gate)
- Date: 2026-09-15
- Relates to: [0012](0012-operator-diagnostics.md), [0013](0013-http-first-agent-clients.md),
  [0019](0019-ops-contract-identity.md), [0030](0030-agent-e2e-primary-surface.md)

## Context

Agent E2E is the primary product surface ([ADR 0030](0030-agent-e2e-primary-surface.md)). Shipped
lanes already cover observation, data, research, and paper/live control, each with `--confirm` on
mutations (live also `--i-understand-live`). Sequencing data → research → optional paper still
required an agent to invent a private workflow. Per-action confirmation is the correct default, but
operators who want less friction on non-live surfaces had no explicit, audited opt-in.

Confirmation remains a CLI/skill-boundary flag. The browser UI does not send `--confirm`. HTTP
mutation routes stay unchanged.

## Decision

Ship Phase 12 as two additive surfaces that do not collapse skill lanes and do not grant live
authority by inheritance.

### Playbook

`thytrader-playbook` sequences existing CLI `main()` functions:

`data healthy → draft/publish → backtest → (optional) paper`

It never constructs a live start and never passes `--i-understand-live`. `--confirm` is forwarded to
child mutation CLIs. Default remains required unless YOLO covers that child tier.

### YOLO

YOLO is operator-enabled, default **off**. Settings:

- `THYTRADER_YOLO_ENABLED` (default `false`)
- `THYTRADER_YOLO_TIERS` — unique subset of `data`, `research`, `paper`

Rejected at startup: `live` as a tier, enabled-without-tiers, tiers-without-enabled, duplicates.

When YOLO covers a tier, HTTP agent CLIs may omit `--confirm` after `GET /api/v1/agent-orchestration`
and `POST /api/v1/agent-orchestration/skipped-confirmations`. The skip writes an info audit on the
existing lane category (`market_data`, `research`, or `runtime`) with action `confirm_skipped`. If
audit storage is disabled, the skip fails closed (HTTP 503) and `--confirm` remains required.

Hard gates never consult YOLO:

- live start, live pause/resume/stop
- `set-risk-policy`
- `thytrader-research --local`

`--confirm` short-circuits before the new routes, so confirmed mutations still work on a
`thytrader-ops-contract-v7` / Alembic `0021` image. YOLO skips and playbook status need the new
prefix and fail closed on HTTP 404 (stale Compose image → `make run`). The ops contract is **not**
bumped.

Schema `thytrader-agent-orchestration-v1` advertises Safe vs YOLO, allowed tiers, `live_hard_gate:
true`, and `live_authority: false`. Operator `configuration` reports the same flags without secrets.

## Consequences

- Agents can run a documented playbook without inventing HTTP.
- Operators can lower confirmation friction on data, research, and paper. Live `--confirm`
  skip arrived later in [ADR 0043](0043-yolo-live-skip-confirm.md).
- Live arming keeps `--i-understand-live`. `--confirm` stayed required for live in this
  slice.
- Skill lanes stay separate. The playbook skill is not an extension of operator, data, research, or
  runtime and does not inherit live authority.
- Phase 11 walk-forward, Phase 13 live extras, Phase 14 journals/notify, on-demand SL/TP, and
  `1m`/`2h` clocks stay out of this slice.

## Alternatives considered

- **HTTP `X-Confirm` header:** rejected; confirmation is a CLI/skill gate, not a browser or raw HTTP
  contract.
- **Bump the ops contract for the new prefix:** rejected; `--confirm` remains sufficient on v7
  images. YOLO and playbook status fail closed on missing routes instead.
- **Live YOLO / inheriting live from paper YOLO:** rejected in this slice. Live kept a
  dedicated `--confirm` hard gate until [ADR 0043](0043-yolo-live-skip-confirm.md) added an
  explicit `live` tier that still never skips `--i-understand-live` and never grants the
  playbook live authority. Paper YOLO still does not inherit live.
- **Playbook calling mutation HTTP directly:** rejected; it must invoke existing lane CLIs so
  authority and stale-image preflights stay in one place.
- **Silent default YOLO on observation skills:** rejected; operator stays read-only.
