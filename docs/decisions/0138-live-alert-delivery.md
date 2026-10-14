# 0138: Alert delivery is degraded while live books run unwatched; signed webhooks

- Status: Accepted
- Date: 2026-10-14
- Relates to: [0115](0115-durable-safety-alerts-and-supervision.md),
  [0130](0130-fleet-entry-health.md)

## Context

ADR 0115 made safety alerts durable and kept external delivery optional, default off
(`notify_provider=none`). The `alert_delivery` component then reported `healthy` with
`DELIVERY_DISABLED`, and operator health had no delivery component at all. An installation
could therefore run live books for days with every alert recorded only in a local feed that
nobody polled, while every health surface read green. The Compose stack also never passed
`THYTRADER_NOTIFY_WEBHOOK_URL` into its containers, so the documented webhook could not be
enabled in a supported install. Webhook receivers had no way to authenticate a POST.

## Decision

1. **Delivery is graded against live exposure.** One grader (`operator/alert_delivery_health.py`)
   serves the `alerts` report and a new `alert_delivery` component in operator health. With no
   external delivery (`none` or `log`):
   - any LIVE book RUNNING or PAUSED → `degraded`, `DELIVERY_DISABLED_WHILE_LIVE`;
   - fleet unreadable → `degraded`, `DELIVERY_DISABLED_LIVE_UNKNOWN` (unknown is never zero);
   - otherwise → `healthy`, `DELIVERY_DISABLED` (paper-only/idle installs keep the default).
   A configured webhook is `healthy` (`DELIVERY_CONFIGURED`) until an open alert's delivery has
   failed (`DELIVERY_FAILED`, `degraded`), now in operator health as well as the alerts report.
   Stopped live books are excluded: managed shutdown keeps protection, but they no longer enter.
2. **Optional HMAC signing.** `THYTRADER_NOTIFY_WEBHOOK_SECRET` (env only, never YAML or reports)
   makes each POST carry `X-Webhook-Timestamp` (Unix seconds) and `X-Webhook-Signature-V2`, the
   hex HMAC-SHA256 of `timestamp + "." + body` over the exact request bytes. Receivers can reject
   forged or replayed alerts. A secret without a URL, or a blank secret, is a configuration error.
3. **Compose passes both webhook variables** to every backend service through the shared
   environment anchor; empty values stay absent. A URL may now be staged while the YAML
   provider is still `none`/`log` (settings previously rejected that, so the only safe enable
   order, URL first, crashed every process). Enable by setting the variables, rebuilding, then
   switching `notify_provider` to `webhook`; a webhook provider without a URL still fails.

No alert codes, trading gates, supervision or retry semantics change. Delivery stays outside
trading cycles and never blocks or alters orders.

## Consequences

- Installations that run live books without a webhook now report degraded health. That is the
  intended signal; it can be cleared only by configuring delivery or by having no live risk.
- Ops contract v98 advertises `runtime_observability: live_alert_delivery_health`.
- A webhook proves an attempted POST and an HTTP success, not that a human read it. A dead-man
  check outside the worker remains future work: a dead worker cannot raise its own alert.

## Alternatives considered

- **Fail health only when alerts are open.** Rejected: the gap is the absence of a pager, which
  matters most before the first alert, not after.
- **Make delivery mandatory for live starts.** Rejected for now: it would refuse risk-reducing
  operator workflows on a monitoring misconfiguration. Degraded health is visible without
  blocking protection.
- **Sign with a library or JWT.** Rejected: a timestamped HMAC over the exact bytes needs only
  the standard library and is simple for any receiver to verify.
