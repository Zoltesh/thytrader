"""Typed contracts for in-app operator chat, distinct from Coinbase credentials."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID  # noqa: TC003 - Pydantic resolves this annotation at runtime.

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

CHAT_SCHEMA_VERSION: Literal["thytrader-operator-chat-v1"] = "thytrader-operator-chat-v1"
CHAT_API_PREFIX = "/api/v1/operator-chat"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_CHAT_MODEL = "gpt-4o-mini"
MAX_TRANSCRIPT_MESSAGES = 80
MAX_TOOL_ROUNDS = 6
MAX_TOOL_RESULT_CHARS = 8_000


class ChatLane(StrEnum):
    """Skill lanes the chat may invoke. Not a collapsed trading brain."""

    OPERATOR = "operator"
    DATA = "data"
    RESEARCH = "research"
    RUNTIME = "runtime"
    PLAYBOOK = "playbook"
    MEMORY = "memory"


class ChatRole(StrEnum):
    """OpenAI-compatible transcript roles stored without secrets."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class LlmProvider(StrEnum):
    """LLM HTTP dialects. Not an exchange list."""

    OPENAI = "openai"
    OPENAI_COMPATIBLE = "openai_compatible"


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class OperatorChatCredentialWrite(_FrozenModel):
    """User-pasted LLM provider settings. Coinbase fields are forbidden extras."""

    provider: LlmProvider = LlmProvider.OPENAI
    model: str = Field(default=DEFAULT_CHAT_MODEL, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, max_length=512)
    api_key: SecretStr = Field(min_length=8, max_length=4096)

    @field_validator("model")
    @classmethod
    def strip_model(cls, value: str) -> str:
        """Reject blank model ids."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("model is required.")
        return stripped

    @field_validator("base_url")
    @classmethod
    def strip_base_url(cls, value: str | None) -> str | None:
        """Treat blank optional URLs as absent."""
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class OperatorChatStatus(_FrozenModel):
    """Redacted chat credential status. Never includes API keys."""

    schema_version: Literal["thytrader-operator-chat-v1"] = CHAT_SCHEMA_VERSION
    llm_configured: bool
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    key_storage: Literal["api_process"] = "api_process"
    coinbase_credentials_in_chat: Literal[False] = False


class LlmToolCall(_FrozenModel):
    """One parsed provider tool call."""

    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, object]


class LlmCompletion(_FrozenModel):
    """One provider turn after secret redaction."""

    content: str | None = None
    tool_calls: tuple[LlmToolCall, ...] = ()


class ChatMessage(_FrozenModel):
    """One redacted transcript row."""

    id: UUID
    role: ChatRole
    content: str
    tool_call_id: str | None = None
    tool_name: str | None = None
    created_at: datetime
    tool_calls: tuple[LlmToolCall, ...] = Field(default=(), exclude=True)


class PendingConfirmation(_FrozenModel):
    """One mutation waiting for the in-app `--confirm` equivalent."""

    id: UUID
    lane: ChatLane
    tool_name: str
    summary: str
    arguments: dict[str, object] = Field(exclude=True)
    requires_understand_live: bool
    tool_call_id: str


class ChatTranscript(_FrozenModel):
    """Current in-process conversation without LLM or Coinbase secrets."""

    schema_version: Literal["thytrader-operator-chat-v1"] = CHAT_SCHEMA_VERSION
    status: OperatorChatStatus
    messages: tuple[ChatMessage, ...]
    pending_confirmations: tuple[PendingConfirmation, ...]


class ChatUserMessage(_FrozenModel):
    """One human chat turn."""

    content: str = Field(min_length=1, max_length=8_000)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        """Reject whitespace-only prompts."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("message content is required.")
        return stripped


class ConfirmationDecision(_FrozenModel):
    """In-app confirmation gate, including the live hard ack when required."""

    confirmed: bool
    i_understand_live: bool = False
