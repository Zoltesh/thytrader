"""Operator-chat inventory adoption tools: catalog, gates and request shapes (ADR 0124)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from fastapi import FastAPI
import pytest

from tests.operator_chat.test_live_acknowledgement import _Capture
from thytrader.config import Settings
from thytrader.operator_chat.loop import _gate, _invoke_tool, _needs_live_ack
from thytrader.operator_chat.tools import split_request, tool_by_name

if TYPE_CHECKING:
    from thytrader.operator_chat.tool_spec import ChatTool


def _tool(name: str) -> ChatTool:
    """A catalog tool that must exist."""
    tool = tool_by_name(name)
    assert tool is not None, name
    return tool


def test_preview_is_a_read_only_query() -> None:
    """The preview never mutates and sends product and clock as query parameters."""
    tool = _tool("runtime_adoption_preview")
    assert tool.method == "GET" and not tool.mutation and tool.live_ack == "never"
    path, query, body = split_request(tool, {"product_id": "DOGE-USDC", "timeframe": "1h"})
    assert path == "/api/v1/inventory-adoptions/preview" and body is None
    assert query == {"product_id": "DOGE-USDC", "timeframe": "1h"}


@pytest.mark.parametrize(
    ("name", "action"), [("runtime_adopt_holdings", "protect"), ("runtime_sell_holdings", "sell")]
)
def test_adopt_and_sell_are_hard_gated_and_always_need_understand_live(
    name: str, action: str
) -> None:
    """YOLO never covers them, and understand-live is required whatever the arguments."""
    tool = _tool(name)
    assert tool.mutation and tool.hard_gate and tool.yolo == "none"
    assert tool.live_ack == "always"
    assert _needs_live_ack(tool, {}) is True
    assert _needs_live_ack(tool, {"mode": "paper"}) is True
    assert tool.properties["action"] == {
        "type": "string",
        "enum": [action],
        "description": f"{action}.",
    }
    assert {"mode", "action", "product_id", "quantity", "idempotency_key"} <= set(tool.required)


@pytest.mark.anyio
async def test_gate_requires_confirmation_and_understand_live_even_with_yolo() -> None:
    """A live adoption always asks the operator; YOLO live does not skip it."""
    settings = Settings(_env_file=None, yolo_enabled=True, yolo_tiers=("paper", "live"))
    confirm, live = await _gate(FastAPI(), settings, _tool("runtime_sell_holdings"), {})
    assert confirm is True and live is True


@pytest.mark.anyio
async def test_only_the_operator_can_acknowledge_live() -> None:
    """A model-supplied acknowledgement is dropped; the ticked checkbox adds it."""
    tool = _tool("runtime_adopt_holdings")
    arguments: dict[str, object] = {
        "mode": "live",
        "action": "protect",
        "product_id": "DOGE-USDC",
        "quantity": "all",
        "stop_price": "0.15",
        "take_profit_price": "0.3",
        "idempotency_key": "chat-1",
        "origin": "agent",
        "i_understand_live": True,
    }
    capture = _Capture()
    with patch("thytrader.operator_chat.loop.invoke_local_json", capture):
        await _invoke_tool(FastAPI(), tool, arguments, ())
        await _invoke_tool(FastAPI(), tool, arguments, (), live_acknowledged=True)
    unticked, ticked = capture.payloads
    assert unticked is not None and "i_understand_live" not in unticked
    assert ticked is not None and ticked["i_understand_live"] is True
    assert ticked["action"] == "protect" and ticked["quantity"] == "all"
