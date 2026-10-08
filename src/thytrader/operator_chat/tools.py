"""Closed catalog of skill-lane HTTP tools. No generic unsigned trading brain.

Assembles the per-lane catalogs (`tool_catalog_*`) in their fixed order, renders them as
OpenAI function tools, maps YOLO bindings to tiers, and turns one tool call into an
HTTP path, query, and JSON body.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.config import YoloTier
from thytrader.operator_chat.tool_catalog_memory import MEMORY_TOOLS
from thytrader.operator_chat.tool_catalog_operator import OPERATOR_TOOLS
from thytrader.operator_chat.tool_catalog_research import DATA_TOOLS, RESEARCH_TOOLS
from thytrader.operator_chat.tool_catalog_runtime import PLAYBOOK_TOOLS, RUNTIME_TOOLS

if TYPE_CHECKING:
    from collections.abc import Mapping

    from thytrader.operator_chat.tool_spec import (
        ChatTool,
        YoloBinding,
    )


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


_QUERY_POST_TOOLS = frozenset({"research_create_strategy", "runtime_stop"})
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
        query: dict[str, str] = {}
        for key, value in rest.items():
            if value is None:
                continue
            if isinstance(value, bool):
                if not value:
                    continue
                query[key] = "true"
            else:
                query[key] = _as_str(value)
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


_TOOLS: tuple[ChatTool, ...] = (
    *OPERATOR_TOOLS,
    *DATA_TOOLS,
    *RESEARCH_TOOLS,
    *RUNTIME_TOOLS,
    *PLAYBOOK_TOOLS,
    *MEMORY_TOOLS,
)

_BY_NAME = {tool.name: tool for tool in _TOOLS}
