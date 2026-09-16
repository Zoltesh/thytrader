# ADR 0061: Application trust boundary for loopback installations

## Status

Accepted

## Context

Stage 3 audit findings F14, F15, F38, F39, and F40 showed that loopback binding alone is
insufficient: any local process could mutate deployments, credentials, settings, and LLM
provider configuration. Client-side `--confirm` and `--i-understand-live` flags were not
enforced on HTTP. Compose credential writes were not durable or visible to workers.
Structured logs dropped exception diagnostics, and YAML settings reload keyed only on float
mtime.

Live financial arming is handled separately by [ADR 0063](0063-stage-5-release-discipline-ci-risk-defaults-rate-budget.md):
live deployments require an operator-published risk policy (`LIVE_REQUIRES_PUBLISHED_POLICY`).
This ADR does not duplicate that gate.

## Decision

1. **Installation credential** — Production enables `trust_boundary_enabled` and requires
   `Authorization: Bearer <installation-token>` on HTTP mutations. The token is persisted
   under `THYTRADER_CREDENTIALS_DIR` when not supplied explicitly.
2. **Host and Origin validation** — Reject hostile `Host` and browser `Origin` headers while
   keeping loopback bind defaults.
3. **CSRF for browser mutations** — Browser-origin writes require double-submit
   `X-CSRF-Token` plus `thytrader_csrf` cookie from `GET /api/v1/security/session`.
4. **Durable credential propagation** — Compose mounts `thytrader_credentials` at
   `/var/lib/thytrader/credentials` for API and workers. Coinbase dotenv writes target that
   volume; workers reload from content-hash changes instead of requiring restart when the
   shared volume is active.
5. **Settings reload** — `SettingsStore` compares YAML content SHA-256 as well as mtime.
6. **Structured logging** — JSON logs include redacted `exception` and `traceback` fields.

Development and test processes keep the boundary off until an installation token is
configured.

## Consequences

- Agent CLIs and the browser UI must send installation auth (and CSRF for browser writes).
- Live runtime mutations still require a published risk policy per ADR 0063 plus CLI
  `--i-understand-live`; this ADR does not add a second live-arm token layer.
- Operators read installation tokens from the credentials directory; they are never echoed
  in API responses, logs, or exceptions.
- No Alembic or ops-contract bump in this slice; another workstream owns `0034+` / `v22+`.
