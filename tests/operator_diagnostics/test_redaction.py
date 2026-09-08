"""Operator report redaction tests."""

from pydantic import SecretStr

from thytrader.config import Settings
from thytrader.operator.redaction import REDACTION, configured_secrets, dumps_redacted, redact_text


def test_redact_text_removes_configured_secrets_and_pem() -> None:
    """Operator output must not echo Coinbase material or PEM blocks."""
    settings = Settings(
        coinbase_api_key_name=SecretStr("organizations/example/apiKeys/secret-name"),
        coinbase_api_private_key=SecretStr("test-private-key-material"),
        _env_file=None,
    )
    secrets = configured_secrets(settings)
    pem = "-----BEGIN EC PRIVATE KEY-----\nABC\n-----END EC PRIVATE KEY-----"
    text = "key=organizations/example/apiKeys/secret-name private=test-private-key-material " + pem
    redacted = redact_text(text, secrets)
    assert "secret-name" not in redacted
    assert "test-private-key-material" not in redacted
    assert "BEGIN EC PRIVATE KEY" not in redacted
    assert REDACTION in redacted


def test_dumps_redacted_strips_database_url_passwords() -> None:
    """JSON dumps must not retain postgres URL passwords even if a payload leaked one."""
    settings = Settings(
        database_url=SecretStr(
            "postgresql+asyncpg://thytrader:super-secret@127.0.0.1:5432/thytrader"
        ),
        _env_file=None,
    )
    payload = {"url": "postgresql+asyncpg://thytrader:super-secret@127.0.0.1:5432/thytrader"}
    rendered = dumps_redacted(payload, configured_secrets(settings))
    assert "super-secret" not in rendered
    assert REDACTION in rendered
