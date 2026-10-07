"""Loopback-only JSON HTTP client for agent CLIs.

Agent commands must talk to the running API on loopback. They must not silently
fall back to PostgreSQL when the API is unreachable.
"""

from __future__ import annotations

import argparse
from http.client import HTTPException
import json
import os
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from thytrader.config import Settings
from thytrader.ops_contract import STALE_IMAGE_REBUILD, ops_contract_matches
from thytrader.security.client import mutation_headers

if TYPE_CHECKING:
    from collections.abc import Sequence

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_MAX_ERROR_BODY_BYTES = 65_536
_MAX_ERROR_CHARS = 1_500
_MAX_VALIDATION_ISSUES = 3
_ENV_BASE_URL = "THYTRADER_API_BASE_URL"
_STALE_OPS_CONTRACT = (
    f"API ops contract does not match this CLI (stale Compose image). {STALE_IMAGE_REBUILD}"
)
_BASE_URL_RESOLUTION = (
    "Agent CLIs use --base-url, then THYTRADER_API_BASE_URL, then THYTRADER_API_HOST and "
    "THYTRADER_API_PORT from settings (default 127.0.0.1:8200)."
)
_SERVER_ERROR_READ_HINT = (
    "The API answered but could not complete the request; retry shortly, and run "
    "`uv run thytrader-operator health` if it persists."
)
_SERVER_ERROR_MUTATION_HINT = (
    "The API answered but could not complete the request; check state before retrying, "
    "and run `uv run thytrader-operator health` if it persists."
)


class AgentHttpError(RuntimeError):
    """Report a redacted loopback API failure without trading authority.

    ``status`` carries the HTTP status code when the failure came from an
    HTTP error response; transport-level failures leave it ``None`` so callers
    can distinguish definitive rejections from ambiguous ones. ``timed_out`` is
    True when the client gave up waiting: the request may still be running on
    the server, so callers must check state before retrying a mutation.
    ``dropped`` is True when the API accepted the connection but closed or reset
    it before a complete answer arrived; that is just as ambiguous for a
    mutation. ``code`` is the API's machine-readable ``detail.code`` when the
    error body carried one.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        timed_out: bool = False,
        dropped: bool = False,
        code: str | None = None,
    ) -> None:
        """Store the message plus the status, transport flags, and API error code."""
        super().__init__(message)
        self.status = status
        self.timed_out = timed_out
        self.dropped = dropped
        self.code = code


def default_api_base_url(settings: Settings) -> str:
    """Build the loopback API origin from process settings."""
    host = "127.0.0.1" if not settings.api_host.is_loopback else str(settings.api_host)
    return f"http://{host}:{settings.api_port}"


def base_url_options() -> argparse.ArgumentParser:
    """Parent parser for the ``--base-url`` flag agent CLIs accept around a subcommand."""
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--base-url",
        default=None,
        help="Loopback API origin. Defaults to THYTRADER_API_BASE_URL or settings.",
    )
    return shared


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


def require_matching_ops_contract(base_url: str) -> None:
    """Fail closed unless `/health/ready` advertises this checkout's ops contract.

    A missing or non-object `ops_contract` is a mismatch. Matching package version
    `0.1.0` is not treated as current. The payload is never default-filled.
    """
    origin = require_loopback_base_url(base_url)
    payload = request_json(method="GET", url=f"{origin}/health/ready")
    if not isinstance(payload, dict):
        raise AgentHttpError(_STALE_OPS_CONTRACT)
    raw_contract = payload.get("ops_contract")
    if not isinstance(raw_contract, dict):
        raise AgentHttpError(_STALE_OPS_CONTRACT)
    mapping = {key: value for key, value in raw_contract.items() if isinstance(key, str)}
    if len(mapping) != len(raw_contract) or not ops_contract_matches(mapping):
        raise AgentHttpError(_STALE_OPS_CONTRACT)


def request_mutation_json(
    *,
    method: str,
    url: str,
    payload: object | None = None,
    timeout: float = 30.0,
    settings: Settings | None = None,
) -> object:
    """Mutate JSON with installation auth when the trust boundary is enabled."""
    resolved = settings if settings is not None else Settings()
    return request_json(
        method=method,
        url=url,
        payload=payload,
        timeout=timeout,
        extra_headers=mutation_headers(resolved),
    )


def request_json(
    *,
    method: str,
    url: str,
    payload: object | None = None,
    timeout: float = 30.0,
    extra_headers: dict[str, str] | None = None,
) -> object:
    """GET or mutate JSON on a previously validated loopback URL."""
    _assert_loopback_request_url(url)
    headers = {"Accept": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    data: bytes | None = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method.upper())  # noqa: S310
    status, raw = _send(request, method=method, url=url, timeout=timeout)
    if status >= 400:
        raise _http_status_error(status, raw, url=url, method=method)
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        path = urlparse(url).path or "/"
        raise AgentHttpError(
            f"ThyTrader API returned a non-JSON body for {method.upper()} {path} "
            f"(HTTP {status}). {STALE_IMAGE_REBUILD} if the API image is stale."
        ) from error


def _send(request: Request, *, method: str, url: str, timeout: float) -> tuple[int, bytes]:
    """Send one request and map every transport failure to a classified AgentHttpError."""
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            return int(response.status), response.read()
    except HTTPError as error:
        raise _http_status_error(error.code, error.read(), url=url, method=method) from error
    except URLError as error:
        if isinstance(error.reason, TimeoutError):
            raise AgentHttpError(
                _timeout_message(method, url, timeout, connected=False), timed_out=True
            ) from error
        raise AgentHttpError(_unreachable_message(url, error.reason)) from error
    except TimeoutError as error:
        # urllib wraps only connect-phase failures in URLError; a server that accepted the
        # request but answered late raises a bare TimeoutError from the response read.
        raise AgentHttpError(
            _timeout_message(method, url, timeout, connected=True), timed_out=True
        ) from error
    except (HTTPException, OSError) as error:
        # A reset, a close before the status line (RemoteDisconnected), or a truncated
        # body is raised from getresponse()/read(), outside urllib's URLError wrapping.
        raise AgentHttpError(_dropped_message(method, url, error), dropped=True) from error


def _http_status_error(status: int, raw: bytes, *, url: str, method: str) -> AgentHttpError:
    """Build the error for one HTTP error response, keeping its API ``detail.code``."""
    message, status_code, code = _http_error_message(status, raw, url=url, method=method)
    return AgentHttpError(message, status=status_code, code=code)


def _unreachable_message(url: str, reason: object) -> str:
    """Say that nothing answered, why, and how the CLI chose this origin."""
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    cause = reason.strerror if isinstance(reason, OSError) and reason.strerror else str(reason)
    return (
        f"ThyTrader API is unreachable at {origin} ({cause}); nothing was sent. Start it "
        f"(`make run`) or target the right port. {_BASE_URL_RESOLUTION}"
    )


def _dropped_message(method: str, url: str, error: BaseException) -> str:
    """Say the API dropped the connection mid-call and whether the request may have run."""
    path = urlparse(url).path or "/"
    call = f"{method.upper()} {path}"
    detail = type(error).__name__
    if method.upper() == "GET":
        return (
            f"The ThyTrader API closed the connection before answering {call} ({detail}); "
            "it may be overloaded or restarting. Nothing was changed; retry the read."
        )
    return (
        f"The ThyTrader API closed the connection before answering {call} ({detail}); "
        "it may be overloaded or restarting. The request may have reached the server; "
        "check state before repeating the mutation."
    )


def _timeout_message(method: str, url: str, timeout: float, *, connected: bool) -> str:
    """Say which call timed out and whether the server may still be running it."""
    path = urlparse(url).path or "/"
    call = f"{method.upper()} {path}"
    if not connected:
        return (
            f"Timed out after {timeout:g} s waiting to connect to the ThyTrader API for "
            f"{call}; it may be busy or restarting. The request was not sent."
        )
    return (
        f"Timed out after {timeout:g} s waiting for the ThyTrader API to answer {call}. "
        "The request was sent and may still be running on the server; the CLI did not "
        "retry it. Check state before repeating a mutation."
    )


def _assert_loopback_request_url(url: str) -> None:
    """Refuse to send a request unless the URL still names a loopback host."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in _LOOPBACK_HOSTS:
        raise AgentHttpError("Agent CLIs may only target a loopback ThyTrader API.")


def _http_error_message(
    status: int, raw: bytes, url: str | None = None, *, method: str = "GET"
) -> tuple[str, int, str | None]:
    """Summarize one HTTP error body without dumping secrets or large payloads.

    The body is parsed before it is shortened, so a long FastAPI ``detail`` still
    yields its ``code`` and ``message`` instead of a cut-off JSON fragment. Returns
    the message, the originating status code, and the API ``detail.code`` (when the
    body has one) so callers can classify the failure without re-parsing the text.
    """
    if status == 404 and url is not None and _stale_image_missing_agent_routes(url):
        return (
            (
                "HTTP 404: agent API routes are missing on a ready listener "
                f"(stale Compose image). {STALE_IMAGE_REBUILD}"
            ),
            status,
            None,
        )
    text = raw[:_MAX_ERROR_BODY_BYTES].decode("utf-8", errors="replace")
    code, detail = _extract_detail(text)
    prefix = f"HTTP {status} {code}" if code else f"HTTP {status}"
    message = f"{prefix}: {_shorten(detail)}"
    if status >= 500:
        hint = _SERVER_ERROR_READ_HINT if method.upper() == "GET" else _SERVER_ERROR_MUTATION_HINT
        message = f"{message} {hint}"
    return (message, status, code)


def _shorten(text: str) -> str:
    """Cap one error detail so a pathological body cannot flood agent output."""
    if len(text) <= _MAX_ERROR_CHARS:
        return text
    return f"{text[: _MAX_ERROR_CHARS - 1]}…"


def _stale_image_missing_agent_routes(url: str) -> bool:
    """True when /health/ready works but a versioned agent route 404s."""
    parsed = urlparse(url)
    path = parsed.path or ""
    if not path.startswith(
        (
            "/api/v1/operator/",
            "/api/v1/data/",
            "/api/v1/strategies",
            "/api/v1/portfolios",
            "/api/v1/backtests",
            "/api/v1/deployments",
            "/api/v1/risk-policy",
            "/api/v1/agent-orchestration",
            "/api/v1/memory",
            "/api/v1/operator-chat",
            "/api/v1/credentials",
        )
    ):
        return False
    ready_url = f"{parsed.scheme}://{parsed.netloc}/health/ready"
    try:
        with urlopen(ready_url, timeout=1.0) as response:  # noqa: S310
            return 200 <= int(response.status) < 300
    except (
        HTTPError,
        URLError,
        OSError,
        TimeoutError,
        ValueError,
    ):
        return False


def _extract_detail(text: str) -> tuple[str | None, str]:
    """Return the FastAPI ``detail`` code (if any) and its human message.

    ``{"detail": "..."}`` and ``{"detail": {"code", "message"}}`` keep their
    text. A request-validation list becomes ``field: problem`` pairs. Anything
    else falls back to the (shortened) raw body.
    """
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError:
        return None, text or "request failed"
    if not isinstance(parsed, dict):
        return None, text or "request failed"
    detail = parsed.get("detail")
    if isinstance(detail, str) and detail:
        return None, detail
    if isinstance(detail, dict):
        raw_code = detail.get("code")
        code = raw_code if isinstance(raw_code, str) and raw_code else None
        message = detail.get("message")
        if isinstance(message, str) and message:
            return code, message
        return code, json.dumps(detail, ensure_ascii=False)
    if isinstance(detail, list) and detail:
        return None, _validation_issues(detail)
    return None, text or "request failed"


def _validation_issues(issues: Sequence[object]) -> str:
    """Render FastAPI's request-validation list as ``loc: msg`` pairs."""
    rendered: list[str] = []
    for issue in issues[:_MAX_VALIDATION_ISSUES]:
        if not isinstance(issue, dict):
            continue
        location = issue.get("loc")
        message = issue.get("msg")
        field = (
            ".".join(str(part) for part in location if part != "body")
            if isinstance(location, list)
            else ""
        )
        text = str(message) if message is not None else "invalid value"
        rendered.append(f"{field}: {text}" if field else text)
    remaining = len(issues) - _MAX_VALIDATION_ISSUES
    if remaining > 0:
        rendered.append(f"and {remaining} more")
    return "; ".join(rendered) or "request failed validation"
