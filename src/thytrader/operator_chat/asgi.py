"""In-process ASGI JSON calls so the chat uses HTTP routes without a nested socket."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from urllib.parse import urlencode

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastapi import FastAPI


class LocalApiError(RuntimeError):
    """Report a failed in-process skill-route invocation."""


async def invoke_local_json(
    app: FastAPI,
    *,
    method: str,
    path: str,
    query: Mapping[str, str] | None = None,
    payload: Mapping[str, object] | None = None,
) -> object:
    """Call one FastAPI route on this process. Loopback sockets are not used."""
    body = b""
    headers: list[tuple[bytes, bytes]] = [
        (b"host", b"127.0.0.1"),
        (b"accept", b"application/json"),
    ]
    if payload is not None:
        body = json.dumps(payload, default=str).encode("utf-8")
        headers.append((b"content-type", b"application/json"))
        headers.append((b"content-length", str(len(body)).encode("ascii")))
    query_string = urlencode(query or {}).encode("ascii")
    status_box: dict[str, int] = {}
    chunks: list[bytes] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.start":
            status_box["status"] = int(message["status"])
            return
        if message["type"] == "http.response.body":
            chunk = message.get("body", b"")
            if isinstance(chunk, bytes) and chunk:
                chunks.append(chunk)

    scope: dict[str, object] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "root_path": "",
        "query_string": query_string,
        "headers": headers,
        "client": ("127.0.0.1", 0),
        "server": ("127.0.0.1", 8200),
        "extensions": {},
    }
    await app(scope, receive, send)
    status = status_box.get("status", 500)
    raw = b"".join(chunks)
    parsed = _parse_body(raw)
    if status >= 400:
        raise LocalApiError(_error_text(status, parsed, raw))
    return parsed


def _parse_body(raw: bytes) -> object:
    """Decode JSON when present; empty bodies become None."""
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return raw.decode("utf-8", errors="replace")[:500]


def _error_text(status: int, parsed: object, raw: bytes) -> str:
    """Summarize a skill-route error without dumping large or secret bodies."""
    if isinstance(parsed, dict):
        detail = parsed.get("detail")
        if isinstance(detail, str) and detail:
            return f"HTTP {status}: {detail}"
        if isinstance(detail, dict):
            message = detail.get("message")
            if isinstance(message, str) and message:
                return f"HTTP {status}: {message}"
    text = raw.decode("utf-8", errors="replace")[:400]
    return f"HTTP {status}: {text or 'request failed'}"
