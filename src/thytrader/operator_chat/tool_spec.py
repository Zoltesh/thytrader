"""The chat tool record and the JSON Schema helpers the lane catalogs share.

`ChatTool` is one allowlisted HTTP skill invocation with its lane, YOLO binding, hard
gate, and live-acknowledgement rule. The `_string`/`_integer` helpers and shared
property schemas keep tool parameter definitions uniform across lanes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from thytrader.operator_chat.models import ChatLane

YoloBinding = Literal["none", "data", "research", "paper", "live", "deployment_mode"]
LiveAck = Literal["never", "when_mode_live", "when_deployment_live", "always"]
"""``always`` is for tools that only act on live, such as inventory adoption (ADR 0124)."""


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
