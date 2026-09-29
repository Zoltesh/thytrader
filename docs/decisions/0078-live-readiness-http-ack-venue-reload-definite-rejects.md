# 0078: Live readiness — HTTP live acknowledgement, execution venue reload, definite rejects, feed-pause recovery

- Status: Accepted
- Date: 2026-09-29
- Relates to: [0030](0030-agent-e2e-primary-surface.md), [0053](0053-workstation-ia-write-only-coinbase-credentials.md), [0058](0058-protection-lifecycle-accounting.md), [0063](0063-stage-5-release-discipline-ci-risk-defaults-rate-budget.md)

## Context

A readiness audit found four blockers to running a strategy live on Coinbase spot:

1. The execution worker built its Coinbase broker, market data, quote reader, and user-feed JWT
   once at startup from environment settings. Credentials set through
   `thytrader-runtime set-coinbase-credentials` or `/settings` (written to the shared credentials
   volume) never reached it, while the credentials API reported that workers reload without
   restart. With no credentials, paper silently evaluated synthetic demo candles.
2. A Coinbase create-order HTTP 4xx (the SDK raises `requests.HTTPError`) became `BrokerError`,
   the order was stored `UNKNOWN` with no venue id, and reconcile re-paused every cycle, so resume
   looped forever even though no order existed.
3. A sub-hour live book paused for user-order-feed staleness stayed paused after the feed
   recovered, and the feed pause could overwrite an operator pause or another mismatch.
4. `--i-understand-live` existed only in the CLI. `POST /api/v1/deployments`, live resume, and
   `POST /api/v1/discretionary-orders` armed live without an explicit acknowledgement.

## Decision

1. **Execution venue reload.** `ExecutionVenueRuntime` rebuilds broker, market data, quote
   reader, and JWT provider when the shared-volume Coinbase secret pair changes (via
   `WorkerCredentialRuntime`, same as the portfolio worker). The worker reads one immutable venue
   generation per cycle, so swaps land between cycles. The user-order feed is stopped and
   restarted per generation. Clearing credentials yields no live broker: live books pause with
   `Live broker is unavailable.` Each binding is audited (`execution_venue_bound` /
   `execution_venue_reloaded`). The operator `runtime` report adds a `DEMO_MARKET_DATA` component
   when credentials are absent and paper books are active. The market-data ingest worker still
   keeps its startup provider until restarted.
2. **Definite vs ambiguous create.** Coinbase create-order HTTP 400/401/403/404/422 prove no
   order exists and become terminal `REJECTED` (`reject_reason=coinbase_http_<status>[:<error>]`,
   audit `order_submit_rejected`); the book continues through the existing rejected-entry path.
   408/409/429/5xx and transport failures stay `UNKNOWN` (audit `order_submit_unconfirmed`).
   Reconcile resolves an id-less `UNKNOWN` order with a bounded List Orders lookup by
   `client_order_id` (product filter, `start_date` five minutes before submit, page cap). Found:
   adopt venue id/status (audit `unconfirmed_order_recovered`). Not found after a complete scan:
   stay `UNKNOWN` and paused with the client order id in `mismatch_detail` (audited once). An
   incomplete scan raises, never "not found". Nothing is ever re-submitted automatically.
3. **Feed-only pause recovery.** The feed gate pauses only a RUNNING book. When the feed is
   connected and fresh again, a book whose only pause reason is the feed (mismatch equals
   `User-order feed is not connected.`, lifecycle command `none`, no breaker latch) resumes with
   audit `user_feed_pause_cleared`. Operator pauses, other mismatches, and latches never clear.
4. **HTTP live acknowledgement.** Live start, live resume, and live discretionary place-order
   require `i_understand_live: true` (strict boolean); otherwise HTTP 428 with detail prefix
   `live_acknowledgement_required:`. No backward-compatible default. The CLI sends it from
   `--i-understand-live` (now also required for `resume` of a live deployment); the web UI sends
   it only after its live confirmation; operator chat drops any model-supplied value and injects
   it only after the understand-live checkbox. Ops contract becomes v40.

## Consequences

Operators can set credentials without restarting the execution worker, and a definitively
rejected order no longer strands a book. Ambiguous submits that Coinbase never shows remain a
manual recovery (verify on Coinbase, then stop and restart the deployment); a dedicated
"resolve unconfirmed order" mutation is a possible follow-up. Clients that armed live over HTTP
without the field must add it. Rebuild the image (`make run`) so CLIs and API agree on v40.

## Alternatives considered

- Treat every 4xx as definite: rejected because 408/409/429 do not prove non-creation.
- Auto-mark a not-found ambiguous order `REJECTED` after a grace period: rejected for now; List
  Orders completeness is not a venue guarantee, so the fail-closed pause stays.
- Restart-only credentials for the execution worker: rejected; the credentials API already
  promised hot reload and operators set keys from the UI.
