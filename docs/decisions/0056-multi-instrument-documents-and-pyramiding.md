# 0056: Multi-instrument strategy documents and intra-strategy pyramiding

- Status: Accepted
- Date: 2026-09-16
- Relates to: [0005](0005-canonical-strategy-schema.md), [0009](0009-deterministic-bar-level-backtest-engine.md),
  [0019](0019-ops-contract-identity.md), [0031](0031-coinbase-first-platform-end-state.md),
  [0033](0033-phase-10-risk-policy-registry.md),
  [0045](0045-spot-shorting-and-attached-entry-brackets.md),
  [0046](0046-shipped-vs-remaining-0031-destination.md),
  [0047](0047-wider-fail-closed-indicator-catalog.md),
  [0048](0048-paper-deploy-fee-fields.md),
  [0049](0049-experiential-train-v1.md),
  [0050](0050-daily-loss-drawdown-rate-collars.md),
  [0051](0051-in-app-operator-chat.md),
  [0052](0052-richer-sweep-axes-study-catalog.md),
  [0053](0053-workstation-ia-write-only-coinbase-credentials.md),
  [0054](0054-trade-reason-journals.md),
  [0055](0055-yaml-settings-runtime-reloadable-yolo.md)

## Context

Phase 10 shipped concurrent **single-instrument** paper/live under one risk-policy registry.
[ADR 0046](0046-shipped-vs-remaining-0031-destination.md) restated that one product per document and
`max_concurrent_positions = 1` still held. Spot shorting
([ADR 0045](0045-spot-shorting-and-attached-entry-brackets.md)) kept one position per deployment and
rejected pyramiding. [ADR 0047](0047-wider-fail-closed-indicator-catalog.md) shipped stochastic, ADX,
configurable rolling inputs, and sample stdev; this slice does not change that catalog.
[ADR 0048](0048-paper-deploy-fee-fields.md) shipped paper deploy maker/taker fields
(`thytrader-ops-contract-v16` / Alembic `0028`). [ADR 0049](0049-experiential-train-v1.md) shipped
the fail-closed experiential trainer (`thytrader-ops-contract-v17` / Alembic `0029`).
[ADR 0050](0050-daily-loss-drawdown-rate-collars.md) shipped daily-loss / drawdown breakers, order-rate
limits, and reference-price collars (`thytrader-ops-contract-v18` / Alembic `0030`).
[ADR 0051](0051-in-app-operator-chat.md) shipped loopback operator chat without bumping the ops
contract. [ADR 0052](0052-richer-sweep-axes-study-catalog.md) shipped richer sweep axes and the
persisted research-study catalog (`thytrader-ops-contract-v19` / Alembic `0031`).
[ADR 0053](0053-workstation-ia-write-only-coinbase-credentials.md) shipped first-class workstation
IA and write-only Coinbase credentials beside the YAML/YOLO `/settings` panel without bumping the
ops contract. This slice does not overwrite those panels. [ADR 0054](0054-trade-reason-journals.md)
shipped why-trade journals (`thytrader-ops-contract-v20` / Alembic `0032`).
[ADR 0055](0055-yaml-settings-runtime-reloadable-yolo.md)
shipped YAML non-secret settings and runtime-reloadable YOLO without bumping the ops contract.
This slice does not revert those contracts.

Vision still requires a published document that can cover several Coinbase USD spot products with
the **same** semantics in backtest, paper, and live, plus explicit same-side adds onto an open
position. Extra exchanges, futures, margin, and borrow-to-short stay out.

## Decision

Keep `schema_version: "1.0"`. Existing single-instrument, `max_open_positions: 1` documents stay
byte-identical. New fields are omitted from canonical JSON when absent.

### Multi-instrument documents

- `instrument` remains the required primary Coinbase `BASE-USD` spot product.
- Optional `additional_instruments` lists 1–7 extra unique USD spot products, disjoint from
  `instrument`. Total coverage is at most eight products.
- The same indicators, `entry.when`, sizing, exits, HTF filter, and per-indicator clocks evaluate
  independently on each covered product.
- `portfolio_limits.max_concurrent_positions` is 1–8 and must not exceed the number of covered
  products. It caps distinct **product** books inside one document. It does not count pyramid adds.
- One paper or live start still creates **one** deployment (one strategy identity per mode, one
  quote cash book). The worker evaluates covered products in lexicographic `product_id` order on
  each shared closed-bar timestamp. Missing or gapped coverage on any covered product pauses.
  Per-product overlay `last_evaluated_bar` is not copied onto the parent row until every covered
  product finishes that shared timestamp.
- Research `dataset_fingerprint` remains the primary instrument. Additional products bind through
  `additional_instrument_datasets` (omitted when empty, lexicographic `product_id`). Each extra
  product requires a complete Coinbase dataset on the decision clock; HTF and extra-TF fingerprints
  are required iff the document declares those clocks.

### Intra-strategy pyramiding

- Omitted `entry.pyramiding` keeps `max_open_positions = 1` (no adds).
- Enabled pyramiding is `{"enabled": true, "require_unrealized_profit": true}` with
  `max_open_positions` 2–8 meaning total fills (initial plus adds) per product.
- Adds are the same side as the open position. Averaging down and martingale stay rejected:
  `require_unrealized_profit` is required and must be `true` (long mark must be above VWAP entry;
  short mark must be below).
- Each add is a new order intent with a unique client order id, then the risk gate, then the
  broker. Timeouts stay `UNKNOWN`; GET-order reconcile; never retry create-order for that client id.
- Adds size from remaining quote cash against the **existing** stop. VWAP updates; stop and target
  are not worsened. Live pyramiding never attaches `trigger_bracket_gtc` on the entry (same as ATR
  trailing): the recorded child OCO is canceled and replaced for the new quantity after the add
  fill.
- Paper and live also require the published risk policy `allow_intra_strategy_pyramiding: true`.
  That flag is omitted from canonical policy JSON when false so compiled-default fingerprints stay
  stable. Schema-enabled pyramiding without the policy flag is denied (`PYRAMIDING_NOT_ALLOWED`).
  Backtest kernels follow the strategy document only (they still do not consume the runtime
  registry). ADR 0050 breakers, rate limits, and collars still gate risk-increasing entries,
  including adds. Pyramid adds skip `MAX_OPEN_POSITIONS` because they occupy an existing product
  book; new product books still count toward `max_concurrent_open_positions`.

Signals still create order intents. Risk-reducing exits are not gated. Coinbase Advanced Trade
stays **SPOT**. Never set `leverage` or `margin_type`. Live shorts still fail closed without
available base.

### Persistence and ops contract

Alembic `0033` stores per-product runtime state and positions (`execution_instrument_state`;
`execution_positions` keyed by `(deployment_id, product_id)` plus `add_count`; `product_id` on
intents and orders). It revises Alembic `0032` (ADR 0054 trade-reason journals). Ops contract
becomes `thytrader-ops-contract-v21` with `multi_instrument_documents` `research`/`paper`/`live`,
`intra_strategy_pyramiding` `research`/`paper`/`live`, and `expected_schema_revision` `0033`.
Paper deploy fee fields, experiential-model engines, risk breakers, order-rate limits,
reference-price collars, the persisted research-study catalog, and trade-reason journals stay on
the contract.

## Consequences

- Operators can publish one fingerprint that trades BTC-USD and ETH-USD together in backtest,
  paper, and live, under the existing registry slots, allowlist, exposure caps, and ADR 0050
  breakers.
- Same-side adds require both the document and the risk policy. Averaging down is not a supported
  mode.
- Extra exchanges, futures, borrow, consecutive-error breakers, leftover study-catalog work stay
  out of this slice. ADR 0053 workstation IA and write-only Coinbase credentials stay beside the
  ADR 0055 YAML/YOLO `/settings` panel. This slice does not overwrite those panels. The ADR 0047
  indicator catalog, ADR 0048 paper fee fields, ADR 0049 trainer, ADR 0050 breakers, ADR 0051
  operator chat, ADR 0052 study catalog, ADR 0054 trade-reason journals, and ADR 0055 YAML/YOLO
  settings stay unchanged.

## Alternatives considered

- **Fan-out N deployments from one document:** rejected; Phase 10 already allows concurrent
  single-instrument identities, and a shared quote book would split cash incorrectly.
- **Bump `schema_version` to 1.1:** rejected; omitted additional instruments and omitted
  pyramiding preserve existing bytes.
- **Count pyramid lots as concurrent positions:** rejected; destination pyramiding is adding to
  one same-side book, not opening a second independent position on that product.
- **Allow averaging down:** rejected; fail closed. Martingale and scale-in on a losing mark stay
  out.
