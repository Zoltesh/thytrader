"""Coinbase-specific configuration behavior."""

from pydantic import ValidationError
import pytest

from thytrader.config import Settings


def test_coinbase_credentials_must_be_configured_as_a_pair() -> None:
    """A partial credential should fail clearly instead of silently selecting demo mode."""
    with pytest.raises(ValidationError, match="must be configured together"):
        Settings(coinbase_api_key_name="organizations/example/apiKeys/example", _env_file=None)


def test_empty_coinbase_placeholders_select_demo_configuration() -> None:
    """Copied empty environment placeholders should normalize to absent credentials."""
    settings = Settings(
        coinbase_api_key_name="",
        coinbase_api_private_key="",
        _env_file=None,
    )

    assert settings.coinbase_api_key_name is None
    assert settings.coinbase_api_private_key is None


def test_coinbase_private_key_expands_escaped_newlines() -> None:
    """Quoted environment values may represent PEM line breaks as escaped newlines."""
    escaped_private_key = "synthetic-key-line-one\\nsynthetic-key-line-two"
    settings = Settings(
        coinbase_api_key_name="organizations/example/apiKeys/example",
        coinbase_api_private_key=escaped_private_key,
        _env_file=None,
    )

    assert settings.coinbase_api_private_key is not None
    assert settings.coinbase_api_private_key.get_secret_value() == (
        "synthetic-key-line-one\nsynthetic-key-line-two"
    )
