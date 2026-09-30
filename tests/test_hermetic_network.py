"""Prove the suite-wide hermetic guards in ``tests/conftest.py`` hold."""

from __future__ import annotations

import socket

from coinbase.rest import RESTClient
import pytest
import requests

from tests.conftest import NetworkAccessBlockedError, is_loopback_host
from thytrader.config import Settings


def test_real_coinbase_rest_calls_fail_loudly() -> None:
    """The official SDK cannot reach api.coinbase.com from any test."""
    client = RESTClient(timeout=1)
    with pytest.raises(NetworkAccessBlockedError, match="stub the Coinbase REST client"):
        client.get_public_product("BTC-USD")


def test_outbound_requests_and_sockets_are_blocked() -> None:
    """Plain requests, DNS, and raw sockets to public hosts all fail closed."""
    with pytest.raises(NetworkAccessBlockedError):
        requests.get("https://api.coinbase.com/api/v3/brokerage/time", timeout=1)
    with pytest.raises(NetworkAccessBlockedError):
        socket.getaddrinfo("api.coinbase.com", 443)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(NetworkAccessBlockedError):
            sock.connect(("104.18.35.15", 443))
        with pytest.raises(NetworkAccessBlockedError):
            sock.connect_ex(("8.8.8.8", 53))


def test_loopback_sockets_still_work() -> None:
    """Local servers (and CI PostgreSQL on localhost) remain reachable."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        with socket.create_connection(("localhost", port), timeout=1):
            pass
    assert is_loopback_host("::1")
    assert not is_loopback_host("api.coinbase.com")


def test_settings_ignore_the_checkout_env_file_and_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never inherit developer credentials or the running stack's database URL."""
    assert Settings.model_config.get("env_file") is None
    settings = Settings()
    assert settings.coinbase_api_key_name is None
    assert settings.coinbase_api_private_key is None
    assert settings.database_url is None
    monkeypatch.setenv("THYTRADER_LOG_LEVEL", "DEBUG")
    assert Settings().log_level == "DEBUG"
