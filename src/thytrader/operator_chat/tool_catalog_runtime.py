"""Runtime and playbook lane chat tools: deployments, orders, and risk policy.

Runtime tools list, show, start, pause, resume, and stop deployments, read their per-bar
decisions, place orders, and read or publish the risk policy, each behind its YOLO
binding, hard gate, and live-acknowledgement rule. The playbook lane only reads the
safe-versus-YOLO advertisement.
"""

from __future__ import annotations

from thytrader.operator_chat.models import ChatLane
from thytrader.operator_chat.tool_spec import (
    _JSON_OBJECT,
    _PRODUCT,
    _TIMEFRAME,
    _UUID,
    ChatTool,
    _integer,
    _opt_string,
    _string,
)

RUNTIME_TOOLS: tuple[ChatTool, ...] = (
    ChatTool(
        name="runtime_list",
        description="List deployments without mutating them.",
        lane=ChatLane.RUNTIME,
        method="GET",
        path="/api/v1/deployments",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={},
        required=(),
    ),
    ChatTool(
        name="runtime_show",
        description="Show one deployment snapshot.",
        lane=ChatLane.RUNTIME,
        method="GET",
        path="/api/v1/deployments/{deployment_id}",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={"deployment_id": _UUID},
        required=("deployment_id",),
    ),
    ChatTool(
        name="runtime_decisions",
        description=(
            "Read-only per-bar decision timeline for one bot, newest first: outcome "
            "(entry_signal, no_signal, holding, exit, entry_blocked, skipped, error), "
            "a one-line reason, rule values versus thresholds, risk verdict, and linked "
            "orders. Optional outcome filter and next_cursor paging."
        ),
        lane=ChatLane.RUNTIME,
        method="GET",
        path="/api/v1/deployments/{deployment_id}/decisions",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={
            "deployment_id": _UUID,
            "outcome": {
                "type": "string",
                "enum": [
                    "entry_signal",
                    "no_signal",
                    "holding",
                    "exit",
                    "entry_blocked",
                    "skipped",
                    "error",
                ],
                "description": "Optional single outcome filter.",
            },
            "limit": _integer("Page size 1..200 (default 50)."),
            "cursor": _opt_string("next_cursor from the previous page."),
        },
        required=("deployment_id",),
    ),
    ChatTool(
        name="runtime_start",
        description=(
            "Start paper or live from a strategy's current rules (by strategy_id). "
            "Live needs understand-live. "
            "Paper may pass maker_fee_rate and taker_fee_rate together (documented assumptions; "
            "omitted paper uses 0.001 / 0.002). Live rejects those fields."
        ),
        lane=ChatLane.RUNTIME,
        method="POST",
        path="/api/v1/deployments",
        mutation=True,
        yolo="deployment_mode",
        hard_gate=False,
        live_ack="when_mode_live",
        properties={
            "strategy_id": _string("Strategy UUID; the server snapshots its current rules."),
            "mode": _string("paper or live."),
            "paper_starting_cash": _opt_string("Paper book cash."),
            "maker_fee_rate": _opt_string(
                "Paper maker fee assumption. Pass with taker_fee_rate. Live rejects."
            ),
            "taker_fee_rate": _opt_string(
                "Paper taker fee assumption. Pass with maker_fee_rate. Live rejects."
            ),
        },
        required=("strategy_id", "mode"),
    ),
    ChatTool(
        name="runtime_pause",
        description="Pause one deployment. Live still needs confirm unless YOLO live.",
        lane=ChatLane.RUNTIME,
        method="POST",
        path="/api/v1/deployments/{deployment_id}/pause",
        mutation=True,
        yolo="deployment_mode",
        hard_gate=False,
        live_ack="never",
        properties={"deployment_id": _UUID},
        required=("deployment_id",),
    ),
    ChatTool(
        name="runtime_resume",
        description=(
            "Resume one paused deployment. Resuming a live deployment re-arms live orders "
            "and needs understand-live."
        ),
        lane=ChatLane.RUNTIME,
        method="POST",
        path="/api/v1/deployments/{deployment_id}/resume",
        mutation=True,
        yolo="deployment_mode",
        hard_gate=False,
        live_ack="when_deployment_live",
        properties={"deployment_id": _UUID},
        required=("deployment_id",),
    ),
    ChatTool(
        name="runtime_stop",
        description=(
            "Stop one deployment. Default is managed shutdown; pass flatten=true to "
            "marketably exit then cancel remainders."
        ),
        lane=ChatLane.RUNTIME,
        method="POST",
        path="/api/v1/deployments/{deployment_id}/stop",
        mutation=True,
        yolo="deployment_mode",
        hard_gate=False,
        live_ack="never",
        properties={
            "deployment_id": _UUID,
            "flatten": {
                "type": "boolean",
                "description": "When true, marketably exit inventory then cancel remainders.",
            },
        },
        required=("deployment_id",),
    ),
    ChatTool(
        name="runtime_place_order",
        description=(
            "On-demand long/short with SL/TP via intent+risk. Live needs understand-live. "
            "Paper may pass maker_fee_rate and taker_fee_rate together. Live rejects those fields."
        ),
        lane=ChatLane.RUNTIME,
        method="POST",
        path="/api/v1/discretionary-orders",
        mutation=True,
        yolo="none",
        hard_gate=True,
        live_ack="when_mode_live",
        properties={
            "mode": _string("paper or live."),
            "product_id": _PRODUCT,
            "stop_price": _string("Stop price."),
            "take_profit_price": _string("Take-profit price."),
            "idempotency_key": _string("Idempotency key."),
            "origin": _string("human or agent."),
            "entry_kind": _opt_string("Default post_only_limit."),
            "timeframe": _TIMEFRAME,
            "side": _opt_string("long or short. Default long."),
            "quantity": _opt_string("Base quantity."),
            "quote_notional": _opt_string("Quote notional."),
            "limit_price": _opt_string("Limit price."),
            "paper_starting_cash": _opt_string("Paper book cash."),
            "maker_fee_rate": _opt_string(
                "Paper maker fee assumption. Pass with taker_fee_rate. Live rejects."
            ),
            "taker_fee_rate": _opt_string(
                "Paper taker fee assumption. Pass with maker_fee_rate. Live rejects."
            ),
        },
        required=(
            "mode",
            "product_id",
            "stop_price",
            "take_profit_price",
            "idempotency_key",
            "origin",
        ),
    ),
    ChatTool(
        name="runtime_adoption_preview",
        description=(
            "Read-only: Coinbase balance of a product's base coin, what live books already "
            "claim of it, the adoptable (unmanaged) quantity, the closed-candle mark, and "
            "protect_blocking_reasons / sell_blocking_reasons. Run before adopting or selling."
        ),
        lane=ChatLane.RUNTIME,
        method="GET",
        path="/api/v1/inventory-adoptions/preview",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={"product_id": _PRODUCT, "timeframe": _TIMEFRAME},
        required=("product_id",),
    ),
    ChatTool(
        name="runtime_adopt_holdings",
        description=(
            "LIVE: adopt coins already held at Coinbase (quantity N or 'all' unmanaged) into "
            "a discretionary long book and rest the stop and take-profit; buys nothing. "
            "Pass mode=live and action=protect. Refused while an occupied discretionary book "
            "trades the product. Always needs confirm and understand-live."
        ),
        lane=ChatLane.RUNTIME,
        method="POST",
        path="/api/v1/inventory-adoptions",
        mutation=True,
        yolo="none",
        hard_gate=True,
        live_ack="always",
        properties={
            "mode": {"type": "string", "enum": ["live"], "description": "Always live."},
            "action": {"type": "string", "enum": ["protect"], "description": "protect."},
            "product_id": _PRODUCT,
            "quantity": _string("Base quantity, or 'all' unmanaged held coins."),
            "stop_price": _string("Stop below the mark."),
            "take_profit_price": _string("Take-profit above the mark."),
            "idempotency_key": _string("Unique key; a repeat returns the original book."),
            "origin": _string("human or agent."),
            "timeframe": _TIMEFRAME,
            "note": _opt_string("Optional why-note on the trade reason."),
        },
        required=(
            "mode",
            "action",
            "product_id",
            "quantity",
            "stop_price",
            "take_profit_price",
            "idempotency_key",
            "origin",
        ),
    ),
    ChatTool(
        name="runtime_sell_holdings",
        description=(
            "LIVE: sell coins already held at Coinbase (quantity N or 'all' unmanaged) into "
            "the product's quote currency. They are adopted into a stopped FLATTEN book that "
            "the worker sells marketably; no protective order is placed. Pass mode=live and "
            "action=sell. Always needs confirm and understand-live."
        ),
        lane=ChatLane.RUNTIME,
        method="POST",
        path="/api/v1/inventory-adoptions",
        mutation=True,
        yolo="none",
        hard_gate=True,
        live_ack="always",
        properties={
            "mode": {"type": "string", "enum": ["live"], "description": "Always live."},
            "action": {"type": "string", "enum": ["sell"], "description": "sell."},
            "product_id": _PRODUCT,
            "quantity": _string("Base quantity, or 'all' unmanaged held coins."),
            "idempotency_key": _string("Unique key; a repeat returns the original book."),
            "origin": _string("human or agent."),
            "timeframe": _TIMEFRAME,
            "note": _opt_string("Optional why-note on the trade reason."),
        },
        required=("mode", "action", "product_id", "quantity", "idempotency_key", "origin"),
    ),
    ChatTool(
        name="runtime_show_risk_policy",
        description="Read the effective risk-policy document.",
        lane=ChatLane.RUNTIME,
        method="GET",
        path="/api/v1/risk-policy",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={},
        required=(),
    ),
    ChatTool(
        name="runtime_set_risk_policy",
        description=(
            "Publish the risk-policy registry including daily-loss, drawdown, order-rate, "
            "collar, and allow_intra_strategy_pyramiding fields. Confirmation-hard-gated. "
            "Does not arm live."
        ),
        lane=ChatLane.RUNTIME,
        method="PUT",
        path="/api/v1/risk-policy",
        mutation=True,
        yolo="none",
        hard_gate=True,
        live_ack="never",
        properties={"payload": _JSON_OBJECT},
        required=("payload",),
    ),
)


PLAYBOOK_TOOLS: tuple[ChatTool, ...] = (
    ChatTool(
        name="playbook_status",
        description="Safe vs YOLO advertisement. Playbook never starts live.",
        lane=ChatLane.PLAYBOOK,
        method="GET",
        path="/api/v1/agent-orchestration",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={},
        required=(),
    ),
)
