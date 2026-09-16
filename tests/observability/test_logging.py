"""Tests for structured application logging."""

from io import StringIO
import json
import logging

from pydantic import SecretStr

from thytrader.config import Settings
from thytrader.observability.logging import (
    build_log_handler,
    set_extra_redacted_secrets,
)


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


def test_log_handler_redacts_process_held_llm_key() -> None:
    """Pasted LLM keys are extra redactions, not Coinbase Settings secrets."""
    llm_key = "sk-test-llm-redaction-value"
    set_extra_redacted_secrets((llm_key,))
    try:
        settings = Settings(_env_file=None)
        stream = StringIO()
        logger = logging.getLogger("thytrader.test.llm")
        logger.handlers.clear()
        logger.propagate = False
        logger.setLevel(logging.INFO)
        logger.addHandler(build_log_handler(settings=settings, stream=stream))
        logger.info("llm key=%s", llm_key)
        rendered = stream.getvalue()
        assert llm_key not in rendered
        assert "[REDACTED]" in rendered
    finally:
        set_extra_redacted_secrets(())


def test_log_handler_includes_redacted_exception_diagnostics() -> None:
    """Structured logs retain exception type and traceback with secret redaction."""
    secret = "super-secret-exception-token"  # noqa: S105 - synthetic fixture material.
    settings = Settings(
        coinbase_api_key_name=SecretStr("organizations/example/apiKeys/example"),
        coinbase_api_private_key=SecretStr(secret),
        _env_file=None,
    )
    stream = StringIO()
    logger = logging.getLogger("thytrader.test.exception")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.ERROR)
    logger.addHandler(build_log_handler(settings=settings, stream=stream))

    def _raise_fixture_error() -> None:
        raise ValueError(f"boom {secret}")

    try:
        _raise_fixture_error()
    except ValueError:
        logger.exception("adapter failed")

    rendered = stream.getvalue()
    payload = json.loads(rendered)
    assert payload["exception"]["type"] == "ValueError"
    assert secret not in rendered
    assert "[REDACTED]" in payload["exception"]["message"]
    assert "[REDACTED]" in payload["traceback"]
