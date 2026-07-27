# 0004: Safe execution and local access defaults

- Status: Superseded by [0006](0006-credential-permission-acceptance.md)
- Date: 2026-07-26

## Context

The user wants low-fee maker trading, early live execution, portable VM installation, and an immediately usable local interface. Maker-only emergency exits can fail to fill. Binding an unauthenticated trading application to every interface can expose financial controls.

## Decision

- Prefer post-only maker execution for entries and ordinary take-profit orders.
- Permit taker or otherwise marketable emergency stop exits; capital protection outranks fee savings.
- Keep live trading disabled until explicitly armed with visible limits.
- Bind to loopback by default.
- Use SSH port forwarding as the initial secure VM-access path.
- Refuse unsafe non-loopback startup unless a protected access mode is explicitly configured.
- Initially require View + Trade Coinbase permissions and reject or block unnecessary Transfer permission.

## Consequences

- Some exits incur taker fees and slippage.
- The strategy/backtest model must distinguish maker intent from emergency execution.
- Synthetic trailing stops require continuous worker health, persisted state, and stale-data controls.
- Remote convenience requires an explicit profile such as future Tailscale support or a properly authenticated TLS deployment.
- Installation remains simple and secure on a workstation.

## Alternatives considered

- **Strict maker-only execution:** rejected because emergency orders may remain unfilled while losses increase.
- **Unauthenticated LAN binding:** rejected because a private LAN is not a sufficient authorization boundary for trading controls.
- **Public TLS/authentication by default:** too many environmental prerequisites for a zero-configuration local install; it remains an explicit mode.
