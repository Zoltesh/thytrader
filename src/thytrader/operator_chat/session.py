"""In-process operator-chat transcript and pending confirmations."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from threading import Lock
from uuid import uuid4

from thytrader.operator_chat.models import (
    MAX_TRANSCRIPT_MESSAGES,
    ChatLane,
    ChatMessage,
    ChatRole,
    ChatTranscript,
    LlmToolCall,
    OperatorChatStatus,
    PendingConfirmation,
)


class OperatorChatSessionStore:
    """One process-lifetime conversation. Secrets never enter the transcript."""

    def __init__(self) -> None:
        """Start with an empty human-visible transcript."""
        self._lock = Lock()
        self._messages: list[ChatMessage] = []
        self._pending: list[PendingConfirmation] = []

    def transcript(self, status: OperatorChatStatus) -> ChatTranscript:
        """Copy the current conversation without credential material."""
        with self._lock:
            messages = tuple(self._messages)
            pending = tuple(self._pending)
        return ChatTranscript(status=status, messages=messages, pending_confirmations=pending)

    def reset(self) -> None:
        """Drop messages and pending mutations for this API process."""
        with self._lock:
            self._messages.clear()
            self._pending.clear()

    def add_message(
        self,
        *,
        role: ChatRole,
        content: str,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        tool_calls: tuple[LlmToolCall, ...] = (),
    ) -> ChatMessage:
        """Append one redacted row and cap transcript length."""
        row = ChatMessage(
            id=uuid4(),
            role=role,
            content=content,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            created_at=datetime.now(UTC),
            tool_calls=tool_calls,
        )
        with self._lock:
            self._messages.append(row)
            overflow = len(self._messages) - MAX_TRANSCRIPT_MESSAGES
            if overflow > 0:
                del self._messages[:overflow]
        return row

    def add_pending(
        self,
        *,
        lane: ChatLane,
        tool_name: str,
        summary: str,
        arguments: dict[str, object],
        requires_understand_live: bool,
        tool_call_id: str,
    ) -> PendingConfirmation:
        """Queue one mutation for the in-app confirmation card."""
        pending = PendingConfirmation(
            id=uuid4(),
            lane=lane,
            tool_name=tool_name,
            summary=summary,
            arguments=arguments,
            requires_understand_live=requires_understand_live,
            tool_call_id=tool_call_id,
        )
        with self._lock:
            self._pending.append(pending)
        return pending

    def pop_pending(self, confirmation_id: object) -> PendingConfirmation | None:
        """Remove and return one pending mutation, if present."""
        with self._lock:
            for index, item in enumerate(self._pending):
                if item.id == confirmation_id:
                    return self._pending.pop(index)
        return None

    def restore_pending(self, pending: PendingConfirmation) -> None:
        """Put a popped confirmation back with the same id."""
        with self._lock:
            if any(item.id == pending.id for item in self._pending):
                return
            self._pending.append(pending)

    def llm_messages(self) -> list[dict[str, object]]:
        """Serialize the transcript for the provider without system prompt."""
        with self._lock:
            rows = tuple(self._messages)
        payload: list[dict[str, object]] = []
        for row in rows:
            payload.append(_provider_message(row))
        return payload


def _provider_message(row: ChatMessage) -> dict[str, object]:
    """Render one stored row as an OpenAI-compatible message object."""
    if row.role is ChatRole.TOOL:
        return {
            "role": "tool",
            "tool_call_id": row.tool_call_id or "",
            "content": row.content,
        }
    item: dict[str, object] = {
        "role": row.role.value,
        "content": row.content or None,
    }
    if row.tool_calls:
        item["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, default=str),
                },
            }
            for call in row.tool_calls
        ]
    return item
