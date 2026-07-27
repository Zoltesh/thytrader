# 0006: Accept Coinbase credentials with additional permissions

- Status: Accepted
- Date: 2026-07-27
- Supersedes: [0004](0004-safe-execution-and-access.md) only for credential permission acceptance

## Context

ThyTrader's initial safety policy rejected Coinbase credentials with permissions beyond View +
Trade. The local operator owns the Coinbase key and may intentionally reuse a key that has
additional permissions. Rejecting that key prevents otherwise safe read-only portfolio use while
providing no protection against behavior that ThyTrader does not implement.

## Decision

- Accept a Coinbase credential when it authorizes the endpoint being requested, regardless of its
  other permissions.
- Report detected permissions to the local operator when Coinbase exposes them, but do not block
  setup because a key has Trade, Transfer, or other additional permissions.
- Keep credentials server-side and never expose them through browser payloads, logs, diagnostics,
  fixtures, or Git.
- Capability safety is enforced by ThyTrader's implemented routes, explicit live arming, risk
  controls, and confirmation boundaries—not by refusing operator-selected credentials.
- The initial portfolio slice remains read-only even when configured with a more capable key.

## Consequences

- A compromised ThyTrader host may expose a more capable Coinbase credential, so host and secret
  hygiene remain important.
- Users can connect View + Trade keys and keys with additional permissions without artificial
  setup failures.
- Future trading features still require explicit arming and must not infer consent from API-key
  permissions.

## Alternatives considered

- **Reject credentials with Transfer or other extra permissions:** rejected because the operator
  explicitly controls credential selection and capability safety belongs at application actions.
- **Require separate keys for each ThyTrader mode:** deferred because it adds setup friction without
  improving the safety of the currently implemented read-only surface.