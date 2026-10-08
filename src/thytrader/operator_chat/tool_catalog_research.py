"""Data and research lane chat tools: market data ingestion and strategy research.

Covers the watchlist, gap inspection and fills, ingestion, strategy templates and
library edits, backtests, studies, and research job status. `tools._TOOLS` lists the
data lane, then the research lane, after the operator lane.
"""

from __future__ import annotations

from thytrader.market_data.lookback import describe_watch_lookback_ceilings
from thytrader.operator_chat.models import ChatLane
from thytrader.operator_chat.tool_spec import (
    _JSON_OBJECT,
    _PRODUCT,
    _TIMEFRAME,
    _UUID,
    ChatTool,
    _integer,
    _opt_string,
)

DATA_TOOLS: tuple[ChatTool, ...] = (
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
                "description": (
                    "Inclusive lookback hours. Per-timeframe ceilings: "
                    f"{describe_watch_lookback_ceilings()}."
                ),
            },
            "enabled": {"type": "boolean", "description": "Whether ingest is enabled."},
        },
        required=("product_id", "timeframe"),
    ),
    ChatTool(
        name="data_ingest",
        description=(
            "Queue complete-only ingest (HTTP 202) for an existing watch. An unwatched "
            "target is refused with HTTP 409: call data_watch_add first. Worker writes Parquet."
        ),
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
        description=(
            "Re-queue complete-only ingest for the same watched target (fill-gaps); "
            "an unwatched target is refused with HTTP 409."
        ),
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
)


RESEARCH_TOOLS: tuple[ChatTool, ...] = (
    ChatTool(
        name="research_list_templates",
        description="List strategy templates.",
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
        name="research_backtest_model",
        description=(
            "Describe the single backtest model's fill, fee, slippage, and optional "
            "spread-stress assumptions (there is no engine selector)."
        ),
        lane=ChatLane.RESEARCH,
        method="GET",
        path="/api/v1/research/backtest-model",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={},
        required=(),
    ),
    ChatTool(
        name="research_list_strategies",
        description="List strategies (validity, current_fingerprint, paper/live status).",
        lane=ChatLane.RESEARCH,
        method="GET",
        path="/api/v1/strategies",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={"limit": _integer("Page size, max 100."), "cursor": _opt_string("Cursor.")},
        required=(),
    ),
    ChatTool(
        name="research_show_strategy",
        description="Show one strategy's document, validation, revision, and current_fingerprint.",
        lane=ChatLane.RESEARCH,
        method="GET",
        path="/api/v1/strategies/{strategy_id}",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={"strategy_id": _UUID},
        required=("strategy_id",),
    ),
    ChatTool(
        name="research_create_strategy",
        description="Create one strategy from a template. Mutation; cannot deploy.",
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
        name="research_delete_strategy",
        description=(
            "Hard-delete one strategy with its backtests, studies, jobs, and paper bots. "
            "Refused (409) while a bot is running or paused; stopped live bots are kept. "
            "Always needs explicit confirmation."
        ),
        lane=ChatLane.RESEARCH,
        method="DELETE",
        path="/api/v1/strategies/{strategy_id}",
        mutation=True,
        yolo="none",
        hard_gate=True,
        live_ack="never",
        properties={"strategy_id": _UUID},
        required=("strategy_id",),
    ),
    ChatTool(
        name="research_submit_backtest",
        description=(
            "Backtest a strategy's current rules: payload names strategy_id plus dataset and "
            "cost assumptions; the response returns the snapshot strategy_fingerprint. The "
            "research worker runs it; a long run answers HTTP 202 with job_id instead, so poll "
            "research_show_job rather than submitting again."
        ),
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
        description=(
            "Submit a composed OOS / walk-forward / sweep / WFO study naming strategies by "
            "strategy_id (candidate_strategy_ids, markets[].strategy_id). A long study answers "
            "HTTP 202 with job_id; poll research_show_job rather than submitting again."
        ),
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
        name="research_show_job",
        description=(
            "Show one research job (backtest or study): status (queued = waiting for a free "
            "research worker), progress, attempts, error_code, and result fingerprints."
        ),
        lane=ChatLane.RESEARCH,
        method="GET",
        path="/api/v1/research/jobs/{job_id}",
        mutation=False,
        yolo="none",
        hard_gate=False,
        live_ack="never",
        properties={"job_id": _UUID},
        required=("job_id",),
    ),
)
