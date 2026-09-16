"""Closed catalog of skill-lane HTTP tools. No generic unsigned trading brain."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from thytrader.agent_orchestration.models import YoloTier
from thytrader.operator_chat.models import ChatLane

if TYPE_CHECKING:
    from collections.abc import Mapping

YoloBinding = Literal["none", "data", "research", "paper", "live", "deployment_mode"]
LiveAck = Literal["never", "when_mode_live"]


@dataclass(frozen=True, slots=True)
class ChatTool:
    """One allowlisted HTTP skill invocation."""

    name: str
    description: str
    lane: ChatLane
    method: str
    path: str
    mutation: bool
    yolo: YoloBinding
    hard_gate: bool
    live_ack: LiveAck
    properties: dict[str, object]
    required: tuple[str, ...]


def chat_tools() -> tuple[ChatTool, ...]:
    """Return the closed tool catalog the LLM may call."""
    return _TOOLS


def openai_tool_schemas() -> list[dict[str, object]]:
    """Render OpenAI function tools from the closed catalog."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": tool.properties,
                    "required": list(tool.required),
                },
            },
        }
        for tool in _TOOLS
    ]


def tool_by_name(name: str) -> ChatTool | None:
    """Look up one catalog entry."""
    return _BY_NAME.get(name)


def yolo_tier_for(binding: YoloBinding, *, mode: str | None) -> YoloTier | None:
    """Map a catalog binding onto an advertised YOLO tier."""
    if binding == "none":
        return None
    if binding == "data":
        return YoloTier.DATA
    if binding == "research":
        return YoloTier.RESEARCH
    if binding == "paper":
        return YoloTier.PAPER
    if binding == "live":
        return YoloTier.LIVE
    if mode == "live":
        return YoloTier.LIVE
    if mode == "paper":
        return YoloTier.PAPER
    return None


def path_keys(path: str) -> tuple[str, ...]:
    """Return `{name}` placeholders in an HTTP path template."""
    keys: list[str] = []
    remainder = path
    while "{" in remainder:
        start = remainder.index("{")
        end = remainder.index("}", start)
        keys.append(remainder[start + 1 : end])
        remainder = remainder[end + 1 :]
    return tuple(keys)


_QUERY_POST_TOOLS = frozenset({"research_create_draft"})
_PAYLOAD_BODY_TOOLS = frozenset(
    {
        "research_submit_backtest",
        "research_plan_study",
        "research_submit_study",
        "runtime_set_risk_policy",
    }
)


def split_request(
    tool: ChatTool,
    arguments: Mapping[str, object],
) -> tuple[str, dict[str, str], dict[str, object] | None]:
    """Build path, query, and JSON body from tool arguments."""
    values = {key: arguments[key] for key in path_keys(tool.path)}
    path = tool.path.format(**{key: _as_str(value) for key, value in values.items()})
    rest = {key: value for key, value in arguments.items() if key not in values}
    if tool.method.upper() == "GET" or tool.name in _QUERY_POST_TOOLS:
        query = {key: _as_str(value) for key, value in rest.items() if value is not None}
        return path, query, None
    body = _json_body(tool.name, rest)
    return path, {}, body


def _json_body(name: str, rest: dict[str, object]) -> dict[str, object] | None:
    """Use `payload` as the HTTP body for composed research/risk writes."""
    if name in _PAYLOAD_BODY_TOOLS:
        nested = rest.get("payload")
        if isinstance(nested, dict):
            return {key: value for key, value in nested.items() if isinstance(key, str)}
        return None
    body = {key: value for key, value in rest.items() if value is not None}
    return body or None


def _as_str(value: object) -> str:
    """Render a path or query value."""
    return str(value)


def _string(description: str) -> dict[str, object]:
    return {"type": "string", "description": description}


def _opt_string(description: str) -> dict[str, object]:
    return {"type": "string", "description": description}


def _integer(description: str) -> dict[str, object]:
    """JSON Schema integer field for optional seeds and counts."""
    return {"type": "integer", "description": description}


_PRODUCT = _string("USD spot product id such as BTC-USD.")
_TIMEFRAME = _string("Venue clock: 1m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, or 1d.")
_UUID = _string("UUID.")
_JSON_OBJECT: dict[str, object] = {
    "type": "object",
    "description": "JSON body for the existing HTTP contract.",
    "additionalProperties": True,
}

_TOOLS: tuple[ChatTool, ...] = (
    ChatTool(
        name="operator_health",
        description="Read-only health. Never trades.",
        lane=ChatLane.OPERATOR,
        method="GET",
        path="/api/v1/operator/health",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={},
        required=(),
    ),
    ChatTool(
        name="operator_configuration",
        description="Redacted configuration. Omits secrets. Coinbase flags are booleans only.",
        lane=ChatLane.OPERATOR,
        method="GET",
        path="/api/v1/operator/configuration",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={},
        required=(),
    ),
    ChatTool(
        name="operator_exchange",
        description="Coinbase connectivity and detected permissions. No balances or keys.",
        lane=ChatLane.OPERATOR,
        method="GET",
        path="/api/v1/operator/exchange",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={},
        required=(),
    ),
    ChatTool(
        name="operator_market_data",
        description="Freshness and gaps for one product and timeframe. Never interpolate.",
        lane=ChatLane.OPERATOR,
        method="GET",
        path="/api/v1/operator/market-data",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={"product_id": _PRODUCT, "timeframe": _TIMEFRAME},
        required=(),
    ),
    ChatTool(
        name="operator_data_catalog",
        description="Local datasets, watchlist, and watch_complete coverage.",
        lane=ChatLane.OPERATOR,
        method="GET",
        path="/api/v1/operator/data-catalog",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={},
        required=(),
    ),
    ChatTool(
        name="operator_products",
        description="Enabled USD spot products.",
        lane=ChatLane.OPERATOR,
        method="GET",
        path="/api/v1/operator/products",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={},
        required=(),
    ),
    ChatTool(
        name="operator_indicators",
        description="Implemented indicator catalog.",
        lane=ChatLane.OPERATOR,
        method="GET",
        path="/api/v1/operator/indicators",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={},
        required=(),
    ),
    ChatTool(
        name="operator_strategies",
        description="Draft, publication, and runtime status without order payloads.",
        lane=ChatLane.OPERATOR,
        method="GET",
        path="/api/v1/operator/strategies",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={},
        required=(),
    ),
    ChatTool(
        name="operator_runtime",
        description="Paper/live status without trading.",
        lane=ChatLane.OPERATOR,
        method="GET",
        path="/api/v1/operator/runtime",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={"deployment_id": _opt_string("Optional deployment UUID.")},
        required=(),
    ),
    ChatTool(
        name="operator_monitor",
        description="Deployments, recent journals, and notify delivery.",
        lane=ChatLane.OPERATOR,
        method="GET",
        path="/api/v1/operator/monitor",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={},
        required=(),
    ),
    ChatTool(
        name="operator_performance",
        description="Backtest or runtime performance slice.",
        lane=ChatLane.OPERATOR,
        method="GET",
        path="/api/v1/operator/performance",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={
            "result_fingerprint": _opt_string("sha256: plus 64 hex."),
            "deployment_id": _opt_string("Deployment UUID."),
        },
        required=(),
    ),
    ChatTool(
        name="operator_risk",
        description="Risk-policy registry identity and pause/mismatch findings. No balances.",
        lane=ChatLane.OPERATOR,
        method="GET",
        path="/api/v1/operator/risk",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={},
        required=(),
    ),
    ChatTool(
        name="operator_reconciliation",
        description="Unknown-order and mismatch findings.",
        lane=ChatLane.OPERATOR,
        method="GET",
        path="/api/v1/operator/reconciliation",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={},
        required=(),
    ),
    ChatTool(
        name="operator_support_bundle",
        description="Redacted diagnostic bundle. Secrets stay omitted.",
        lane=ChatLane.OPERATOR,
        method="GET",
        path="/api/v1/operator/support-bundle",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={},
        required=(),
    ),
    ChatTool(
        name="data_watchlist_list",
        description="List ingestion watch targets.",
        lane=ChatLane.DATA,
        method="GET",
        path="/api/v1/data/watchlist",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={},
        required=(),
    ),
    ChatTool(
        name="data_inspect_gaps",
        description="Classify missing bars. Never interpolate.",
        lane=ChatLane.DATA,
        method="GET",
        path="/api/v1/data/gaps",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={"product_id": _PRODUCT, "timeframe": _TIMEFRAME},
        required=("product_id", "timeframe"),
    ),
    ChatTool(
        name="data_watch_add",
        description="Upsert one watch target. Mutation; confirmation-gated.",
        lane=ChatLane.DATA,
        method="PUT",
        path="/api/v1/data/watchlist",
        mutation=True,
        yolo="data",
        hard_gate=False,
        live_ack="never",
        properties={
            "product_id": _PRODUCT,
            "timeframe": _TIMEFRAME,
            "lookback_hours": {
                "type": "integer",
                "description": "Inclusive lookback hours.",
            },
            "enabled": {"type": "boolean", "description": "Whether ingest is enabled."},
        },
        required=("product_id", "timeframe"),
    ),
    ChatTool(
        name="data_ingest",
        description="Queue complete-only ingest (HTTP 202). Worker writes Parquet.",
        lane=ChatLane.DATA,
        method="POST",
        path="/api/v1/data/ingest",
        mutation=True,
        yolo="data",
        hard_gate=False,
        live_ack="never",
        properties={"product_id": _PRODUCT, "timeframe": _TIMEFRAME},
        required=("product_id", "timeframe"),
    ),
    ChatTool(
        name="data_fill_gaps",
        description="Re-queue complete-only ingest for the same target (fill-gaps).",
        lane=ChatLane.DATA,
        method="POST",
        path="/api/v1/data/ingest",
        mutation=True,
        yolo="data",
        hard_gate=False,
        live_ack="never",
        properties={"product_id": _PRODUCT, "timeframe": _TIMEFRAME},
        required=("product_id", "timeframe"),
    ),
    ChatTool(
        name="research_list_templates",
        description="List research draft templates.",
        lane=ChatLane.RESEARCH,
        method="GET",
        path="/api/v1/research/templates",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={},
        required=(),
    ),
    ChatTool(
        name="research_engine_support",
        description="V1/V2/V3 engine-support matrix.",
        lane=ChatLane.RESEARCH,
        method="GET",
        path="/api/v1/research/engine-support",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={},
        required=(),
    ),
    ChatTool(
        name="research_create_draft",
        description="Create a research template draft. Mutation; cannot deploy.",
        lane=ChatLane.RESEARCH,
        method="POST",
        path="/api/v1/strategies",
        mutation=True,
        yolo="research",
        hard_gate=False,
        live_ack="never",
        properties={
            "product_id": _opt_string("Default BTC-USD."),
            "timeframe": _opt_string("Default 1h."),
            "template": _opt_string("Template id such as ema-trend."),
        },
        required=(),
    ),
    ChatTool(
        name="research_publish",
        description="Publish the current draft immutably. Does not start paper or live.",
        lane=ChatLane.RESEARCH,
        method="POST",
        path="/api/v1/strategies/{strategy_id}/publish",
        mutation=True,
        yolo="research",
        hard_gate=False,
        live_ack="never",
        properties={"strategy_id": _UUID},
        required=("strategy_id",),
    ),
    ChatTool(
        name="research_submit_backtest",
        description="Submit an idempotent backtest against a published strategy and dataset.",
        lane=ChatLane.RESEARCH,
        method="POST",
        path="/api/v1/backtests",
        mutation=True,
        yolo="research",
        hard_gate=False,
        live_ack="never",
        properties={"payload": _JSON_OBJECT},
        required=("payload",),
    ),
    ChatTool(
        name="research_plan_study",
        description="Plan a composed research study without submitting it.",
        lane=ChatLane.RESEARCH,
        method="POST",
        path="/api/v1/research/studies/plan",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={"payload": _JSON_OBJECT},
        required=("payload",),
    ),
    ChatTool(
        name="research_submit_study",
        description="Submit a composed OOS / walk-forward / sweep / WFO study.",
        lane=ChatLane.RESEARCH,
        method="POST",
        path="/api/v1/research/studies",
        mutation=True,
        yolo="research",
        hard_gate=False,
        live_ack="never",
        properties={"payload": _JSON_OBJECT},
        required=("payload",),
    ),
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
        name="runtime_start",
        description=(
            "Start paper or live for a published fingerprint. Live needs understand-live. "
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
            "strategy_fingerprint": _string("sha256: plus 64 hex."),
            "mode": _string("paper or live."),
            "paper_starting_cash": _opt_string("Paper book cash."),
            "maker_fee_rate": _opt_string(
                "Paper maker fee assumption. Pass with taker_fee_rate. Live rejects."
            ),
            "taker_fee_rate": _opt_string(
                "Paper taker fee assumption. Pass with maker_fee_rate. Live rejects."
            ),
        },
        required=("strategy_fingerprint", "mode"),
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
        description="Resume one paused deployment.",
        lane=ChatLane.RUNTIME,
        method="POST",
        path="/api/v1/deployments/{deployment_id}/resume",
        mutation=True,
        yolo="deployment_mode",
        hard_gate=False,
        live_ack="never",
        properties={"deployment_id": _UUID},
        required=("deployment_id",),
    ),
    ChatTool(
        name="runtime_stop",
        description="Stop one deployment.",
        lane=ChatLane.RUNTIME,
        method="POST",
        path="/api/v1/deployments/{deployment_id}/stop",
        mutation=True,
        yolo="deployment_mode",
        hard_gate=False,
        live_ack="never",
        properties={"deployment_id": _UUID},
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
            "Publish the risk-policy registry including daily-loss, drawdown, order-rate, and "
            "collar fields. Confirmation-hard-gated. Does not arm live."
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
    ChatTool(
        name="memory_status",
        description="Journal/hook counts and redacted notify configuration.",
        lane=ChatLane.MEMORY,
        method="GET",
        path="/api/v1/memory",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={},
        required=(),
    ),
    ChatTool(
        name="memory_monitor",
        description="Memory monitor snapshot.",
        lane=ChatLane.MEMORY,
        method="GET",
        path="/api/v1/memory/monitor",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={},
        required=(),
    ),
    ChatTool(
        name="memory_list_journals",
        description="List newest-first journals.",
        lane=ChatLane.MEMORY,
        method="GET",
        path="/api/v1/memory/journals",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={
            "origin": _opt_string("human or agent."),
            "kind": _opt_string("fact, lesson, or note."),
        },
        required=(),
    ),
    ChatTool(
        name="memory_add_journal",
        description="Append an origin-attributed journal. YOLO never covers memory.",
        lane=ChatLane.MEMORY,
        method="POST",
        path="/api/v1/memory/journals",
        mutation=True,
        yolo="none",
        hard_gate=True,
        live_ack="never",
        properties={
            "origin": _string("human or agent."),
            "kind": _string("fact, lesson, or note."),
            "title": _string("Title."),
            "body": _string("Body."),
            "product_id": _opt_string("Optional product."),
            "runtime_mode": _opt_string("none, backtest, paper, or live."),
            "lesson_outcome": _opt_string("Required for lessons."),
        },
        required=("origin", "kind", "title", "body"),
    ),
    ChatTool(
        name="memory_add_sentiment",
        description="Append a sentiment snapshot. YOLO never covers memory.",
        lane=ChatLane.MEMORY,
        method="POST",
        path="/api/v1/memory/sentiment",
        mutation=True,
        yolo="none",
        hard_gate=True,
        live_ack="never",
        properties={
            "origin": _string("human or agent."),
            "label": _string("bullish, bearish, neutral, or unknown."),
            "product_id": _opt_string("Optional product."),
            "note": _opt_string("Optional note."),
        },
        required=("origin", "label"),
    ),
    ChatTool(
        name="memory_add_pattern",
        description="Append a pattern-learning hook. YOLO never covers memory.",
        lane=ChatLane.MEMORY,
        method="POST",
        path="/api/v1/memory/patterns",
        mutation=True,
        yolo="none",
        hard_gate=True,
        live_ack="never",
        properties={
            "origin": _string("human or agent."),
            "pattern_key": _string("Stable pattern key."),
            "name": _string("Display name."),
            "hypothesis": _string("Hypothesis."),
            "status": _string("hypothesized, supported, contradicted, or retired."),
        },
        required=("origin", "pattern_key", "name", "hypothesis", "status"),
    ),
    ChatTool(
        name="memory_notify",
        description="Request a user notification. YOLO never covers memory.",
        lane=ChatLane.MEMORY,
        method="POST",
        path="/api/v1/memory/notifications",
        mutation=True,
        yolo="none",
        hard_gate=True,
        live_ack="never",
        properties={
            "origin": _string("human or agent."),
            "title": _string("Title."),
            "body": _string("Body."),
            "severity": _opt_string("info, warning, or critical."),
        },
        required=("origin", "title", "body"),
    ),
    ChatTool(
        name="memory_list_models",
        description=(
            "List trained experiential models. Advisory research input only, not a live brain."
        ),
        lane=ChatLane.MEMORY,
        method="GET",
        path="/api/v1/memory/models",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={},
        required=(),
    ),
    ChatTool(
        name="memory_show_model",
        description="Show one trained experiential model. Advisory only.",
        lane=ChatLane.MEMORY,
        method="GET",
        path="/api/v1/memory/models/{model_id}",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={"model_id": _UUID},
        required=("model_id",),
    ),
    ChatTool(
        name="memory_train",
        description=(
            "Train thytrader-experiential-train-v1 from attributed local journals. "
            "Confirmation-hard-gated. Advisory research input only; never a live brain."
        ),
        lane=ChatLane.MEMORY,
        method="POST",
        path="/api/v1/memory/models",
        mutation=True,
        yolo="none",
        hard_gate=True,
        live_ack="never",
        properties={
            "origin": _string("human or agent."),
            "seed": _integer("Optional RNG seed. Default 1."),
        },
        required=("origin",),
    ),
)

_BY_NAME = {tool.name: tool for tool in _TOOLS}
