"""Tests for dotenv persistence of Coinbase credentials."""

from pathlib import Path

from thytrader.credentials.envfile import (
    assignment_line,
    clear_coinbase_env,
    env_file_writable,
    is_managed_assignment,
    quote_env_value,
    upsert_coinbase_env,
)


def test_quote_env_value_escapes_newlines_and_quotes() -> None:
    """PEM line breaks become \\n sequences inside a quoted assignment."""
    assert quote_env_value('line-one\nline-two"x') == '"line-one\\nline-two\\"x"'


def test_upsert_preserves_unrelated_lines_and_replaces_keys(tmp_path: Path) -> None:
    """Existing comments and unrelated keys survive a credentials write."""
    path = tmp_path / ".env"
    path.write_text(
        "# keep me\nTHYTRADER_LOG_LEVEL=INFO\nTHYTRADER_COINBASE_API_KEY_NAME=old\n",
        encoding="utf-8",
    )
    upsert_coinbase_env(path=path, key_name="organizations/example/apiKeys/new", private_key="k1\nk2")
    text = path.read_text(encoding="utf-8")
    assert "# keep me" in text
    assert "THYTRADER_LOG_LEVEL=INFO" in text
    assert "old" not in text
    assert 'THYTRADER_COINBASE_API_KEY_NAME="organizations/example/apiKeys/new"' in text
    assert 'THYTRADER_COINBASE_API_PRIVATE_KEY="k1\\nk2"' in text
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_clear_writes_empty_placeholders(tmp_path: Path) -> None:
    """Clearing writes empty quoted values rather than deleting the keys."""
    path = tmp_path / ".env"
    upsert_coinbase_env(path=path, key_name="name", private_key="secret")
    clear_coinbase_env(path=path)
    text = path.read_text(encoding="utf-8")
    assert 'THYTRADER_COINBASE_API_KEY_NAME=""' in text
    assert 'THYTRADER_COINBASE_API_PRIVATE_KEY=""' in text
    assert "secret" not in text


def test_env_file_writable_false_for_missing_parent(tmp_path: Path) -> None:
    """A missing parent directory is not writable."""
    assert env_file_writable(tmp_path / "missing" / ".env") is False


def test_managed_assignment_detects_export_prefix() -> None:
    """export KEY=value lines are treated as managed Coinbase assignments."""
    assert is_managed_assignment("export THYTRADER_COINBASE_API_KEY_NAME=x")
    assert not is_managed_assignment("THYTRADER_LOG_LEVEL=INFO")
    assert assignment_line("THYTRADER_COINBASE_API_KEY_NAME", "n") == (
        'THYTRADER_COINBASE_API_KEY_NAME="n"\n'
    )
