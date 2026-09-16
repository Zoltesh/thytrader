# 0048: In-app operator chat over gated skill lanes

- Status: Accepted
- Date: 2026-09-16
- Relates to: [0006](0006-credential-permission-acceptance.md),
  [0012](0012-operator-diagnostics.md), [0013](0013-http-first-agent-clients.md),
  [0030](0030-agent-e2e-primary-surface.md),
  [0034](0034-phase-12-agent-orchestration-yolo.md),
  [0043](0043-yolo-live-skip-confirm.md)

## Context

[ADR 0030](0030-agent-e2e-primary-surface.md) makes confirmation-gated skills and versioned HTTP
the primary product surface. Destination product also includes a loopback **in-app operator chat**
where the user pastes **their** LLM API key. That chat must be an operator over the same lanes as
`ops/`, not a second unsigned trading brain.

Coinbase credentials are a separate surface (destination Coinbase secrets UI). Extra exchanges stay
waiting. Nested HTTP from the API worker back to the same Uvicorn listener would deadlock.

[ADR 0047](0047-wider-fail-closed-indicator-catalog.md) already shipped the wider fail-closed
indicator catalog. This ADR does not reopen that catalog.

## Decision

Ship loopback operator chat as an additive `/api/v1/operator-chat` contract plus the `/chat` UI.

1. **LLM keys are not Coinbase keys.** The user pastes an OpenAI or OpenAI-compatible API key into
   `PUT /api/v1/operator-chat/credentials`. Storage is process-lifetime `SecretStr` on the API,
   `extra="forbid"`. Status and transcripts never echo the secret. Coinbase hosts, PEM-shaped
   material, and Coinbase field names are rejected. Restarting the API clears the key.
2. **Closed tool catalog.** The model may call only named tools that map onto existing skill-lane
   HTTP routes (operator reads; data / research / runtime / memory mutations; playbook status).
   There is no generic code-exec or unsigned order path.
3. **In-process ASGI.** Tool calls invoke those routes on the same FastAPI app without opening a
   nested loopback socket.
4. **The same gates as the CLIs.** Mutations wait for in-app confirmation (`--confirm`) unless YOLO
   covers that tier and the skipped-confirmation audit succeeds. Live start and live place-order
   always need `i_understand_live`. Memory, live place-order, and `set-risk-policy` stay
   confirmation-hard-gated. The playbook never starts live.
5. **Operator CLI.** `thytrader-operator chat-status` is HTTP-only (`GET /api/v1/operator-chat/status`).
   `--local` is rejected. This is not an operator-report-v1 envelope and does not bump the ops
   contract.

No extra exchanges. Workstation IA of existing pages and the Coinbase secrets UI stay other slices.

## Consequences

- Skills must document `chat-status` and that `/chat` uses the same HTTP lanes; operators must not
  invent a second catalog.
- LLM keys are lost on API restart by design.
- Coinbase keys never enter browser payloads on this surface.
- [ADR 0046](0046-shipped-vs-remaining-0031-destination.md) and
  [ADR 0047](0047-wider-fail-closed-indicator-catalog.md) are unchanged; this ADR only ships the
  in-app operator chat destination row.

## Alternatives considered

- **Collapse lanes into one chat brain:** rejected; ADR 0030 lane splits remain.
- **Persist the LLM key in PostgreSQL:** rejected; process-lifetime matches paste-and-use and
  avoids a new secret store.
- **Nested HTTP to the same Uvicorn worker:** rejected; it deadlocks.
- **Reuse Coinbase Settings for the LLM key:** rejected; sibling Coinbase secrets UI owns those
  credentials.
- **Add extra exchanges in this slice:** rejected; Coinbase Advanced Trade spot only.
