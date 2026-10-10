"""Time every Coinbase REST request an SDK session sends (ADR 0131).

``instrument_rest_client`` mounts ``TimedHTTPAdapter`` on the official SDK client's
``requests`` session, so market-data, broker and balance reads that share the client are
all counted at one boundary. Each request is recorded into the bound
``VenueCallLedger`` (a no-op when none is bound) as its method, a redacted endpoint shape
and its latency; query strings, bodies, headers and identifiers are never kept.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from requests.adapters import HTTPAdapter

from thytrader.observability.venue_calls import record_venue_call

if TYPE_CHECKING:
    from requests.models import PreparedRequest, Response

_API_PREFIX = "/api/v3/brokerage"
_HTTP_ERROR_FLOOR = 400
_MAX_ENDPOINT_LENGTH = 128

type _Timeout = float | tuple[float | None, float | None] | None
type _Verify = bool | str
type _Cert = str | tuple[str, str] | None


def endpoint_shape(url: str | None) -> str:
    """Return the request path with product, order and account ids replaced by ``{id}``.

    A path segment holding a digit or a hyphen (``BTC-USDC``, a UUID, an order id) is an
    identifier; Advanced Trade's fixed path words use letters and underscores only.
    """
    path = urlsplit(url or "").path
    if path.startswith(_API_PREFIX):
        path = path[len(_API_PREFIX) :]
    segments = [
        "{id}" if any(char.isdigit() or char == "-" for char in segment) else segment
        for segment in path.split("/")
    ]
    shaped = "/".join(segments) or "/"
    return shaped[:_MAX_ENDPOINT_LENGTH]


class TimedHTTPAdapter(HTTPAdapter):
    """Default ``requests`` transport that also records each request's latency."""

    def send(
        self,
        request: PreparedRequest,
        stream: bool = False,
        timeout: _Timeout = None,
        verify: _Verify = True,
        cert: _Cert = None,
        proxies: dict[str, str] | None = None,
    ) -> Response:
        """Send one request, recording a failure when it raises or returns an HTTP error."""
        method = (request.method or "GET").upper()[:8]
        endpoint = endpoint_shape(request.url)
        started = time.perf_counter()
        try:
            response = super().send(
                request, stream=stream, timeout=timeout, verify=verify, cert=cert, proxies=proxies
            )
        except Exception:
            record_venue_call(method, endpoint, time.perf_counter() - started, ok=False)
            raise
        ok = response.status_code < _HTTP_ERROR_FLOOR
        record_venue_call(method, endpoint, time.perf_counter() - started, ok=ok)
        return response


def instrument_rest_client(client: object) -> None:
    """Mount the timing adapter on an SDK client's HTTPS session, when it has one.

    The official ``coinbase.rest.RESTClient`` keeps a ``requests.Session`` as ``session``;
    any other client (a test double) is left unchanged.
    """
    session = getattr(client, "session", None)
    mount = getattr(session, "mount", None)
    if callable(mount):
        mount("https://", TimedHTTPAdapter())
