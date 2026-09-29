"""Operator chat passes i_understand_live only after the understand-live checkbox."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from fastapi import FastAPI
import pytest

from thytrader.operator_chat.loop import _invoke_tool, _needs_live_ack
from thytrader.operator_chat.tools import tool_by_name

if TYPE_CHECKING:
    from collections.abc import Mapping

_FINGERPRINT = "sha256:" + "ab" * 32


class _Capture:
    """Record the in-process HTTP body the chat tool sends."""

    def __init__(self) -> None:
        """Start with no captured body."""
        self.payloads: list[Mapping[str, object] | None] = []

    async def __call__(
        self,
        app: FastAPI,
        *,
        method: str,
        path: str,
        query: Mapping[str, str] | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> object:
        """Capture the body and return an empty JSON object."""
        del app, method, path, query
        self.payloads.append(payload)
        return {}


@pytest.mark.anyio
async def test_model_supplied_acknowledgement_is_dropped() -> None:
    """The LLM cannot self-acknowledge live trading through tool arguments."""
    tool = tool_by_name("runtime_start")
    assert tool is not None
    capture = _Capture()
    with patch("thytrader.operator_chat.loop.invoke_local_json", capture):
        await _invoke_tool(
            FastAPI(),
            tool,
            {"strategy_fingerprint": _FINGERPRINT, "mode": "live", "i_understand_live": True},
            (),
        )
    body = capture.payloads[0]
    assert body is not None
    assert "i_understand_live" not in body


@pytest.mark.anyio
async def test_confirmed_understand_live_injects_acknowledgement() -> None:
    """After the operator ticks understand-live, the body carries i_understand_live=true."""
    tool = tool_by_name("runtime_place_order")
    assert tool is not None
    capture = _Capture()
    with patch("thytrader.operator_chat.loop.invoke_local_json", capture):
        await _invoke_tool(
            FastAPI(),
            tool,
            {"mode": "live", "product_id": "BTC-USDC"},
            (),
            live_acknowledged=True,
        )
    body = capture.payloads[0]
    assert body is not None
    assert body["i_understand_live"] is True


@pytest.mark.anyio
async def test_live_resume_body_carries_acknowledgement_only_when_confirmed() -> None:
    """runtime_resume is a body-less POST unless understand-live was ticked."""
    tool = tool_by_name("runtime_resume")
    assert tool is not None
    capture = _Capture()
    arguments: dict[str, object] = {"deployment_id": "11111111-1111-1111-1111-111111111111"}
    with patch("thytrader.operator_chat.loop.invoke_local_json", capture):
        await _invoke_tool(FastAPI(), tool, arguments, ())
        await _invoke_tool(FastAPI(), tool, arguments, (), live_acknowledged=True)
    assert capture.payloads[0] is None
    assert capture.payloads[1] == {"i_understand_live": True}


def test_resume_requires_understand_live_for_live_or_unknown_books() -> None:
    """Live resume needs understand-live; an unresolvable mode fails closed."""
    tool = tool_by_name("runtime_resume")
    assert tool is not None
    assert tool.live_ack == "when_deployment_live"
    assert _needs_live_ack(tool, {}, deployment_mode="live") is True
    assert _needs_live_ack(tool, {}, deployment_mode=None) is True
    assert _needs_live_ack(tool, {}, deployment_mode="paper") is False


def test_pause_and_stop_never_need_understand_live() -> None:
    """Risk-reducing lifecycle tools keep live_ack=never."""
    for name in ("runtime_pause", "runtime_stop"):
        tool = tool_by_name(name)
        assert tool is not None
        assert _needs_live_ack(tool, {}, deployment_mode="live") is False
