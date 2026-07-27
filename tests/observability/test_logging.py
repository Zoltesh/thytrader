"""Tests for structured application logging."""

from io import StringIO
import json
import logging

from pydantic import SecretStr

from thytrader.config import Settings
from thytrader.observability.logging import build_log_handler


def test_log_handler_emits_json_without_configured_secrets() -> None:
    """Structured logs should redact configured Coinbase credentials."""
    api_key_name = "organizations/example/apiKeys/example"
    private_key = "test-private-key-material"
    settings = Settings(
        coinbase_api_key_name=SecretStr(api_key_name),
        coinbase_api_private_key=SecretStr(private_key),
        _env_file=None,
    )
    stream = StringIO()
    logger = logging.getLogger("thytrader.test")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(build_log_handler(settings=settings, stream=stream))

    logger.info("credentials key=%s private=%s", api_key_name, private_key)

    rendered = stream.getvalue()
    payload = json.loads(rendered)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "thytrader.test"
    assert payload["message"] == "credentials key=[REDACTED] private=[REDACTED]"
    assert api_key_name not in rendered
    assert private_key not in rendered
