"""Signed HTTP transport for Coinbase Advanced Trade REST v3 JSON."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping


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


class RestClientTransport:
    """Adapt coinbase-advanced-py RESTClient into a JSON-only transport."""

    def __init__(self, client: object) -> None:
        """Wrap one SDK REST client used only for CDP-signed HTTP."""
        self._client = client

    def get(self, path: str, params: Mapping[str, object] | None = None) -> dict[str, Any]:
        """GET a documented Advanced Trade path."""
        getter = getattr(self._client, "get")  # noqa: B009 - duck-typed SDK client
        return json_object(getter(path, params=dict(params or {})))

    def post(self, path: str, data: Mapping[str, object] | None = None) -> dict[str, Any]:
        """POST a documented Advanced Trade path."""
        poster = getattr(self._client, "post")  # noqa: B009 - duck-typed SDK client
        return json_object(poster(path, data=dict(data or {})))
