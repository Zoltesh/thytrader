"""Suite-wide hermetic guards: no real network, no developer ``.env``, no Coinbase.

Every test runs with three autouse protections:

* ``Settings`` ignores the checkout's ``.env`` and inherits no ``THYTRADER_*`` variables
  from the developer shell (the PostgreSQL test URLs below are the only exceptions), so
  a test can never pick up real Coinbase credentials or the running stack's database.
* Outbound sockets are blocked: ``connect``/``connect_ex`` to a non-loopback address
  and DNS resolution of a non-loopback hostname raise :class:`NetworkAccessBlockedError`.
  Loopback (TestClient-free local servers, CI PostgreSQL on localhost) and Unix sockets
  still work.
* ``requests`` (the transport under the official Coinbase SDK) refuses any non-loopback
  URL before DNS, with a message that names the stub to use instead.

A test that genuinely needs the real network must opt in with ``@pytest.mark.allow_network``
and a documented reason. Subprocess-launching tests must pass an explicit environment.
"""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import socket
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

from pydantic_settings import DotEnvSettingsSource
import pytest
import requests

from thytrader.config import Settings

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_KEPT_ENVIRONMENT = frozenset({"THYTRADER_TEST_DATABASE_URL", "THYTRADER_INTEGRATION_DATABASE_URL"})
_LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})
_CHECKOUT_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class NetworkAccessBlockedError(RuntimeError):
    """Raised when a test tries to reach a non-loopback host."""


def is_loopback_host(host: object) -> bool:
    """Return whether one socket host (name or literal address) stays on this machine."""
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    if not isinstance(host, str):
        return False
    text = host.strip("[]").split("%", 1)[0].lower()
    if not text or text in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def _blocked(target: object) -> NetworkAccessBlockedError:
    """Build the loud failure a test sees when it tries to leave the machine."""
    return NetworkAccessBlockedError(
        f"Test attempted outbound network access to {target!r}. Tests are hermetic: stub the "
        "Coinbase REST client (inject a fake CoinbaseClient/transport) or patch the HTTP call. "
        "Mark with @pytest.mark.allow_network only with a documented reason."
    )


def _guard_address(sock: socket.socket, address: object) -> None:
    """Reject INET/INET6 connections whose destination is not loopback."""
    if sock.family not in (socket.AF_INET, socket.AF_INET6):
        return
    host = address[0] if isinstance(address, tuple) and address else address
    if not is_loopback_host(host):
        raise _blocked(address)


@pytest.fixture(autouse=True)
def hermetic_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ``Settings`` from reading the checkout ``.env`` or the developer shell."""
    # SettingsConfigDict is a TypedDict; monkeypatch.setitem needs a plain mutable mapping.
    config = cast("dict[str, object]", Settings.model_config)
    monkeypatch.setitem(config, "env_file", None)
    original_read = DotEnvSettingsSource._read_env_file

    def isolated_read(source: DotEnvSettingsSource, file_path: Path) -> Mapping[str, str | None]:
        """Ignore the checkout dotenv even when a settings store explicitly selects it."""
        if (
            file_path.absolute() == _CHECKOUT_ENV_FILE
            or file_path.resolve() == _CHECKOUT_ENV_FILE.resolve()
        ):
            return {}
        return original_read(source, file_path)

    monkeypatch.setattr(DotEnvSettingsSource, "_read_env_file", isolated_read)
    for name in tuple(os.environ):
        if name.startswith("THYTRADER_") and name not in _KEPT_ENVIRONMENT:
            monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def block_outbound_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly on any non-loopback socket connection, DNS lookup, or requests call."""
    if request.node.get_closest_marker("allow_network") is not None:
        return
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo
    original_send = requests.Session.send

    def guarded_connect(self: socket.socket, address: object) -> None:
        _guard_address(self, address)
        original_connect(self, cast("tuple[str, int]", address))

    def guarded_connect_ex(self: socket.socket, address: object) -> int:
        _guard_address(self, address)
        return original_connect_ex(self, cast("tuple[str, int]", address))

    def guarded_getaddrinfo(host: object, *args: object, **kwargs: object) -> object:
        if host is not None and not is_loopback_host(host):
            raise _blocked(host)
        resolve = cast("Callable[..., object]", original_getaddrinfo)
        return resolve(host, *args, **kwargs)

    def guarded_send(
        self: requests.Session, prepared: requests.PreparedRequest, **kwargs: object
    ) -> requests.Response:
        host = urlsplit(prepared.url or "").hostname
        if not is_loopback_host(host):
            raise _blocked(prepared.url)
        send = cast("Callable[..., requests.Response]", original_send)
        return send(self, prepared, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(requests.Session, "send", guarded_send)
