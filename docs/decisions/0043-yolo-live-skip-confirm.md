# 0043: YOLO skip-confirm for live start/pause/resume/stop

- Status: Accepted
- Date: 2026-09-16
- Relates to: [0030](0030-agent-e2e-primary-surface.md),
  [0034](0034-phase-12-agent-orchestration-yolo.md)
- Supersedes in part: [0034](0034-phase-12-agent-orchestration-yolo.md) (live `--confirm`
  hard gate only)

## Context

Phase 12 shipped default-off YOLO so agents can skip `--confirm` on `data`, `research`, and
`paper` after an audited skip. Live start/pause/resume/stop still required `--confirm` even
when the operator wanted the same friction reduction as paper. `--i-understand-live` already
exists as the dedicated live-money acknowledgement. Playbook isolation, kill switches, live
place-order, risk-policy publication, `--local` research, and memory were not the remaining
gap.

ADR 0034 rejected live as a YOLO tier so paper YOLO could not inherit live authority. That
rejection should not block an explicit, operator-enabled `live` tier.

## Decision

Add `live` to `THYTRADER_YOLO_TIERS`. When YOLO is enabled and `live` is advertised,
`thytrader-runtime` may omit `--confirm` on live **start, pause, resume, and stop** after
`GET /api/v1/agent-orchestration` and `POST /api/v1/agent-orchestration/skipped-confirmations`.
The skip writes `confirm_skipped` on the existing `runtime` audit category. If YOLO is off,
the live tier is absent, or audit storage is unavailable, the skip fails closed and
`--confirm` remains required.

Hard gates that never consult YOLO:

- `--i-understand-live` on live start and live place-order
- live `place-order` `--confirm`
- `set-risk-policy`
- `thytrader-research --local`
- `thytrader-memory` mutations
- kill switches and individual venue-order cancellation (unchanged; still not this skill)

Paper YOLO never covers a live deployment. Live YOLO never covers paper. Playbook still
never constructs `--mode live` or `--i-understand-live`, including when `live` is advertised.
Schema `thytrader-agent-orchestration-v1` keeps `live_hard_gate: true` and
`live_authority: false` to mean the acknowledgement flag and playbook isolation, not that
`--confirm` is ineligible for the `live` tier. The ops contract is **not** bumped.

## Consequences

- Operators can skip `--confirm` on live control the same way as data/research/paper.
- Live money still requires `--i-understand-live`.
- Skill lanes stay separate. The playbook does not inherit live authority from YOLO.
- Kill switches, disarming, and venue-order cancellation are unchanged.

## Alternatives considered

- **Skip `--i-understand-live` under YOLO:** rejected; that flag is the live-money gate.
- **Inherit live from paper YOLO:** rejected; tiers stay independent.
- **YOLO live place-order and set-risk-policy:** rejected for this slice; those stay
  `--confirm` hard-gated.
- **Playbook live start when YOLO live is on:** rejected; playbook never starts live.
- **Bump the ops contract:** rejected; `--confirm` remains sufficient on older images;
  YOLO skips fail closed on missing routes.
