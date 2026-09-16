# Order intent → risk → broker

A signal or discretionary action creates an **order intent**. It does not call
Coinbase directly. Models: `thytrader.execution.models.OrderIntent`,
`thytrader.risk.models.RiskPolicyDefinition`, `PaperBroker`,
`CoinbaseBroker`.

Entries are gated by the risk-policy registry **before** intent persist. Exits
are not. Timeouts persist `unknown` and GET-order reconcile; they are not proof
of failure. Unique `client_order_id`. Live start and live place-order need
`--i-understand-live`.

```mermaid
flowchart TD
  Src["Strategy closed-bar signal\nor discretionary place-order"] --> IntentDraft["Build OrderIntent\nclient_order_id unique"]
  IntentDraft --> Risk["RiskPolicyDefinition\nentries only"]
  Risk -->|DENY| Skip["Skip the bar / HTTP deny\nno persist"]
  Risk -->|ALLOW| Persist["Persist intent first"]
  Persist --> Mode{"deployment.mode"}
  Mode -->|paper| Paper["PaperBroker\nsynthetic SL/TP\nnever venue brackets"]
  Mode -->|live| Live["CoinbaseBroker REST v3\nspot only"]
  Live --> Attach{"SL/TP known and\ntrailing disabled?"}
  Attach -->|yes| Attached["attached_order_configuration\ntrigger_bracket_gtc"]
  Attach -->|no| PostFill["post-fill OCO\nADR 0036"]
  Paper --> Order["Order + Fill + Position"]
  Attached --> Order
  PostFill --> Order
  Live --> Timeout["network timeout"]
  Timeout --> Unknown["status unknown"]
  Unknown --> Reconcile["GET-order reconcile\nbefore any retry"]
```

```mermaid
classDiagram
  class Deployment {
    kind strategy|discretionary
    mode paper|live
    status running|paused|stopped
    phase flat|pending_entry|open|pending_exit
    product_id
    timeframe venue clock
    strategy_fingerprint?
  }
  class OrderIntent {
    id
    client_order_id
    purpose entry|take_profit|stop|time_exit|bracket
    side buy|sell
    kind post_only_limit|marketable|trigger_bracket
    quantity Decimal
    origin human|agent|runtime
    idempotency_key?
    stop_trigger_price?
    take_profit_price?
  }
  class Order {
    intent_id
    venue_order_id?
    status pending|open|filled|canceled|rejected|unknown
  }
  class Fill {
    venue_fill_id
    price quantity fee
  }
  class Position {
    side long|short
    quantity entry_price
    stop_price target_price
  }
  class RiskPolicyDefinition {
    schema_version thytrader-risk-policy-v1
    product_allowlist
    max_concurrent_running_deployments
    max_concurrent_open_positions
    exposure fractions
    paper_capital_quote
    allocations
  }
  class RiskVerdict {
    decision allow|deny
    reason_code
  }
  Deployment --> OrderIntent
  OrderIntent --> Order
  Order --> Fill
  Deployment --> Position : at most one
  RiskPolicyDefinition --> RiskVerdict
```

Live shorts fail closed without available base (`INSUFFICIENT_BASE_FOR_SPOT_SHORT`).
Never `leverage`, `margin_type`, or futures. Nonempty allocations deny
discretionary. Stale data or unhealthy required connections block new
risk-increasing orders.
