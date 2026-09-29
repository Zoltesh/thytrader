"""Signed HTTP transport for Coinbase Advanced Trade REST v3 JSON."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_ERROR_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


class SignedHttpTransport(Protocol):
    """Issue authenticated GET/POST calls and return untyped JSON mappings."""

    def get(self, path: str, params: Mapping[str, object] | None = None) -> dict[str, Any]:
        """GET one REST path and return the JSON object body."""
        ...

    def post(self, path: str, data: Mapping[str, object] | None = None) -> dict[str, Any]:
        """POST one REST path and return the JSON object body."""
        ...


def json_object(payload: object) -> dict[str, Any]:
    """Normalize an SDK or raw response into a plain JSON object."""
    if isinstance(payload, dict):
        return {str(key): value for key, value in payload.items()}
    to_dict = getattr(payload, "to_dict", None)
    if callable(to_dict):
        converted = to_dict()
        if isinstance(converted, dict):
            return {str(key): value for key, value in converted.items()}
    raise TypeError("Coinbase REST response is not a JSON object.")


class CoinbaseHttpStatusError(OSError):
    """An HTTP error status Coinbase returned for one REST call.

    Subclasses ``OSError`` so every existing transport-failure handler still
    treats it as a failed request. Callers that need to distinguish a definite
    venue rejection from an ambiguous outcome inspect ``status_code``.

    Attributes:
        status_code: HTTP status from the venue response.
        error_code: Coinbase ``error`` / ``error_response.error`` token, when present
            and shaped like an enum identifier. Never raw response text.
    """

    def __init__(self, status_code: int, error_code: str | None) -> None:
        """Record a redacted status and error token."""
        super().__init__(f"Coinbase REST returned HTTP {status_code}.")
        self.status_code = status_code
        self.error_code = error_code


def http_status_error(error: OSError) -> CoinbaseHttpStatusError | None:
    """Narrow an SDK ``requests.HTTPError`` into a redacted status error.

    The Coinbase SDK raises ``requests.HTTPError`` (an ``OSError``) carrying the
    ``response``. Anything without an integer status (timeouts, resets, DNS) is
    not an HTTP status outcome and returns None.
    """
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    if not isinstance(status_code, int) or isinstance(status_code, bool):
        return None
    return CoinbaseHttpStatusError(status_code, _error_code(response))


def _error_code(response: object) -> str | None:
    """Extract a Coinbase error enum token from an error response body, if well formed."""
    text = getattr(response, "text", None)
    if not isinstance(text, str) or not text:
        return None
    try:
        parsed: object = json.loads(text)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    candidates: list[object] = [parsed.get("error")]
    nested = parsed.get("error_response")
    if isinstance(nested, dict):
        candidates.insert(0, nested.get("error"))
    for candidate in candidates:
        if isinstance(candidate, str) and _ERROR_CODE.fullmatch(candidate):
            return candidate
    return None


def _call_with_status(call: Callable[[], object]) -> dict[str, Any]:
    """Run one SDK call and re-raise HTTP status failures as ``CoinbaseHttpStatusError``."""
    try:
        return json_object(call())
    except CoinbaseHttpStatusError:
        raise
    except OSError as error:
        status_error = http_status_error(error)
        if status_error is None:
            raise
        raise status_error from None


class RestClientTransport:
    """Adapt coinbase-advanced-py RESTClient into a JSON-only transport."""

    def __init__(self, client: object) -> None:
        """Wrap one SDK REST client used only for CDP-signed HTTP."""
        self._client = client

    def get(self, path: str, params: Mapping[str, object] | None = None) -> dict[str, Any]:
        """GET a documented Advanced Trade path."""
        getter = getattr(self._client, "get")  # noqa: B009 - duck-typed SDK client
        return _call_with_status(lambda: getter(path, params=dict(params or {})))

    def post(self, path: str, data: Mapping[str, object] | None = None) -> dict[str, Any]:
        """POST a documented Advanced Trade path."""
        poster = getattr(self._client, "post")  # noqa: B009 - duck-typed SDK client
        return _call_with_status(lambda: poster(path, data=dict(data or {})))
