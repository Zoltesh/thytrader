"""HTTP helper for read-only operator chat status."""

from __future__ import annotations

from thytrader.agent_http import request_json
from thytrader.operator_chat.models import CHAT_API_PREFIX, OperatorChatStatus


def fetch_chat_status(base_url: str) -> OperatorChatStatus:
    """GET redacted LLM-key status. Never returns the secret."""
    payload = request_json(method="GET", url=f"{base_url}{CHAT_API_PREFIX}/status")
    return OperatorChatStatus.model_validate(payload)
