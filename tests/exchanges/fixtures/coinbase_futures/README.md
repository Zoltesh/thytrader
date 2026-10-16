# Coinbase CFM futures order and fill fixtures (synthetic)

Every file here is synthetic. None of them is a copy of a real account's response, and no id,
price, fee or timestamp belongs to a real order, fill or account. They exist so the live futures
order path (ADR 0134) is built and tested against the venue's real payload shapes.

Each file is `{"provenance": "...", "body": {...}}`; `body` is the response body.

- **Shape observed, values invented.** `orders_historical_batch.json`,
  `orders_historical_fills.json` and `cfm_positions_flat.json` follow the field names, nesting
  and value formats of a GET-only read of Advanced Trade REST v3 futures responses
  (`orders/historical/batch`, `orders/historical/fills`, `cfm/positions`). Ids are obviously
  fake (`00000000-0000-4000-8000-…`), prices are round (2500 and 2490) and dated 2026-01-05.
  Each commission is recomputed for one 0.1-unit contract as
  `0.10% x price x 0.1 + 0.10 venue + 0.01 clearing` (ADR 0133).
- **Shape observed in the supervised probes (2026-10-16, ADR 0134 §7), values invented.**
  `create_order_post_only_limit_accepted.json`, `create_order_stop_limit_accepted.json`,
  `create_order_trigger_bracket_accepted.json`, `order_attached_bracket_resting.json`,
  `close_position_success.json`, `close_position_failure.json` (an HTTP 404 body, with
  `http_status`), `preview_reduce_only_not_allowed.json` and
  `preview_trigger_bracket_without_position.json`. Ids re-keyed, prices rounded.
- **Hand-built from the public API reference.** `fill_combo_future_legs.json`,
  `fill_size_in_quote.json` and `create_order_fcm_session_rejected.json` follow the
  documented schemas of List Fills (`future_legs`, `size_in_quote`) and Create Order
  (`NewOrderFailureReason`). They have not been observed from the venue; replace them with
  sanitized observations when the supervised probes run.

`tests/exchanges/test_coinbase_futures_fixtures.py` pins the facts later slices rely on. Never
commit a raw venue capture here: re-key ids, round prices and move timestamps first, and keep
`tests/test_no_operator_data.py` passing.
