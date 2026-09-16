"""OpenAI-compatible chat completions client for operator chat."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from thytrader.operator_chat.models import LlmCompletion, LlmToolCall

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.operator_chat.credentials import StoredLlmCredentials

_TIMEOUT_SECONDS = 60.0
_MAX_ERROR_CHARS = 400


class LlmClient(Protocol):
    """Outbound LLM completions. Implementations must not log the API key."""

    async def complete(
        self,
        *,
        credentials: StoredLlmCredentials,
        messages: Sequence[dict[str, object]],
        tools: Sequence[dict[str, object]],
    ) -> LlmCompletion:
        """Return one assistant turn."""
        ...


class LlmClientError(RuntimeError):
    """Report a redacted provider failure."""


class OpenAICompatibleClient:
    """POST /chat/completions against the stored LLM base URL."""

    async def complete(
        self,
        *,
        credentials: StoredLlmCredentials,
        messages: Sequence[dict[str, object]],
        tools: Sequence[dict[str, object]],
    ) -> LlmCompletion:
        """Issue one completion. Runs urllib off the event loop via to_thread."""
        return await asyncio.to_thread(
            _complete_sync,
            credentials,
            list(messages),
            list(tools),
        )


def _complete_sync(
    credentials: StoredLlmCredentials,
    messages: list[dict[str, object]],
    tools: list[dict[str, object]],
) -> LlmCompletion:
    """Synchronous provider call used from a worker thread."""
    url = f"{credentials.base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": credentials.model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {credentials.api_key.get_secret_value()}",
    }
    request = Request(url, data=body, headers=headers, method="POST")  # noqa: S310
    try:
        with urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
            raw = response.read()
            status = int(response.status)
    except HTTPError as error:
        raise LlmClientError(_http_error(error.code, error.read())) from error
    except URLError as error:
        raise LlmClientError("LLM provider is unreachable.") from error
    if status >= 400:
        raise LlmClientError(_http_error(status, raw))
    try:
        parsed: object = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as error:
        raise LlmClientError("LLM provider returned non-JSON.") from error
    return _parse_completion(parsed)


def _http_error(status: int, raw: bytes) -> str:
    """Summarize a provider error without echoing Authorization material."""
    text = raw.decode("utf-8", errors="replace")[:_MAX_ERROR_CHARS]
    return f"LLM HTTP {status}: {text or 'request failed'}"


def _parse_completion(parsed: object) -> LlmCompletion:
    """Extract content and tool calls from a Chat Completions body."""
    if not isinstance(parsed, dict):
        raise LlmClientError("LLM provider returned an unexpected payload.")
    choices = parsed.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LlmClientError("LLM provider returned no choices.")
    first = choices[0]
    if not isinstance(first, dict):
        raise LlmClientError("LLM provider returned an unexpected choice.")
    message = first.get("message")
    if not isinstance(message, dict):
        raise LlmClientError("LLM provider returned no message.")
    content = message.get("content")
    text = content if isinstance(content, str) else None
    return LlmCompletion(content=text, tool_calls=_parse_tool_calls(message.get("tool_calls")))


def _parse_tool_calls(raw: object) -> tuple[LlmToolCall, ...]:
    """Parse function tool calls; invalid JSON arguments become empty objects."""
    if not isinstance(raw, list):
        return ()
    calls: list[LlmToolCall] = []
    for item in raw:
        parsed = _one_tool_call(item)
        if parsed is not None:
            calls.append(parsed)
    return tuple(calls)


def _one_tool_call(item: object) -> LlmToolCall | None:
    """Parse one tool call object."""
    if not isinstance(item, dict):
        return None
    identifier = item.get("id")
    function = item.get("function")
    if not isinstance(identifier, str) or not isinstance(function, dict):
        return None
    name = function.get("name")
    if not isinstance(name, str) or not name:
        return None
    arguments = _decode_arguments(function.get("arguments"))
    return LlmToolCall(id=identifier, name=name, arguments=arguments)


def _decode_arguments(raw: object) -> dict[str, object]:
    """Accept a JSON object or JSON string of arguments."""
    if isinstance(raw, dict):
        return {key: value for key, value in raw.items() if isinstance(key, str)}
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed, dict):
        return {key: value for key, value in parsed.items() if isinstance(key, str)}
    return {}
