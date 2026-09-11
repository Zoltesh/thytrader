"""Loopback-only JSON HTTP client for agent CLIs.

Agent commands must talk to the running API on loopback. They must not silently
fall back to PostgreSQL when the API is unreachable.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from thytrader.config import Settings

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_MAX_ERROR_CHARS = 500
_ENV_BASE_URL = "THYTRADER_API_BASE_URL"


class AgentHttpError(RuntimeError):
    """Report a redacted loopback API failure without trading authority."""


def default_api_base_url(settings: Settings) -> str:
    """Build the loopback API origin from process settings."""
    host = "127.0.0.1" if not settings.api_host.is_loopback else str(settings.api_host)
    return f"http://{host}:{settings.api_port}"


def resolve_api_base_url(*, explicit: str | None, settings: Settings) -> str:
    """Resolve `--base-url`, then `THYTRADER_API_BASE_URL`, then settings."""
    if explicit:
        return require_loopback_base_url(explicit)
    env_value = os.environ.get(_ENV_BASE_URL)
    if env_value:
        return require_loopback_base_url(env_value)
    return require_loopback_base_url(default_api_base_url(settings))


def require_loopback_base_url(url: str) -> str:
    """Reject non-loopback origins so agent CLIs cannot target a remote host."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise AgentHttpError("Agent CLIs require an http(s) loopback --base-url.")
    if parsed.hostname not in _LOOPBACK_HOSTS:
        raise AgentHttpError("Agent CLIs may only target a loopback ThyTrader API.")
    return f"{parsed.scheme}://{parsed.netloc}"


def request_json(
    *,
    method: str,
    url: str,
    payload: object | None = None,
    timeout: float = 30.0,
) -> object:
    """GET or mutate JSON on a previously validated loopback URL."""
    _assert_loopback_request_url(url)
    headers = {"Accept": "application/json"}
    data: bytes | None = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method.upper())  # noqa: S310
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            status = int(response.status)
            raw = response.read()
    except HTTPError as error:
        raise AgentHttpError(_http_error_message(error.code, error.read(), url=url)) from error
    except URLError as error:
        raise AgentHttpError(
            f"ThyTrader API is unreachable at {url}. Start thytrader-api or pass --local."
        ) from error
    if status >= 400:
        raise AgentHttpError(_http_error_message(status, raw, url=url))
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as error:
        raise AgentHttpError("ThyTrader API returned non-JSON.") from error


def _assert_loopback_request_url(url: str) -> None:
    """Refuse to send a request unless the URL still names a loopback host."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in _LOOPBACK_HOSTS:
        raise AgentHttpError("Agent CLIs may only target a loopback ThyTrader API.")


def _http_error_message(status: int, raw: bytes, url: str | None = None) -> str:
    """Summarize one HTTP error body without dumping secrets or large payloads."""
    if status == 404 and url is not None and _stale_image_missing_agent_routes(url):
        return (
            "HTTP 404: agent API routes are missing on a ready listener "
            "(stale Compose image). Rebuild and restart with `make run`."
        )
    text = raw.decode("utf-8", errors="replace")[:_MAX_ERROR_CHARS]
    detail = _extract_detail(text)
    return f"HTTP {status}: {detail}"


def _stale_image_missing_agent_routes(url: str) -> bool:
    """True when /health/ready works but a versioned agent route 404s."""
    parsed = urlparse(url)
    path = parsed.path or ""
    if not path.startswith(
        (
            "/api/v1/operator/",
            "/api/v1/data/",
            "/api/v1/strategies",
            "/api/v1/backtests",
            "/api/v1/deployments",
        )
    ):
        return False
    ready_url = f"{parsed.scheme}://{parsed.netloc}/health/ready"
    try:
        with urlopen(ready_url, timeout=1.0) as response:  # noqa: S310
            return 200 <= int(response.status) < 300
    except HTTPError, URLError, OSError, TimeoutError, ValueError:
        return False


def _extract_detail(text: str) -> str:
    """Prefer FastAPI `detail` when the error body is JSON."""
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError:
        return text or "request failed"
    if isinstance(parsed, dict):
        detail = parsed.get("detail")
        if isinstance(detail, str) and detail:
            return detail
        if isinstance(detail, dict):
            message = detail.get("message")
            if isinstance(message, str) and message:
                return message
            return json.dumps(detail, ensure_ascii=False)
    return text or "request failed"
