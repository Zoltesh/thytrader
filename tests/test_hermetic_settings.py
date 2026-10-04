"""Regression coverage for settings stores explicitly selecting the checkout dotenv."""

from pathlib import Path

from pydantic_settings import DotEnvSettingsSource

from thytrader.config import Settings
from thytrader.settings_yaml import SettingsStore

_ROOT = Path(__file__).resolve().parents[1]


def test_explicit_checkout_dotenv_is_ignored(tmp_path: Path) -> None:
    """Explicit dotenv selection cannot bypass the suite's developer-file isolation."""
    source = DotEnvSettingsSource(Settings, env_file=None)
    assert source._read_env_file(_ROOT / ".env") == {}
    settings = SettingsStore.open(tmp_path / "settings.yaml", env_file=_ROOT / ".env").current()
    assert settings.database_url is None
    assert settings.coinbase_api_key_name is None
    assert settings.coinbase_api_private_key is None


def test_explicit_temporary_dotenv_remains_available(tmp_path: Path) -> None:
    """Tests can deliberately select harmless dotenv fixtures outside the checkout."""
    fixture = tmp_path / ".env"
    fixture.write_text("THYTRADER_API_PORT=8543\n", encoding="utf-8")
    settings = SettingsStore.open(tmp_path / "settings.yaml", env_file=fixture).current()
    assert settings.api_port == 8543


def test_checkout_dotenv_alias_is_also_ignored(tmp_path: Path) -> None:
    """An explicitly selected symlink cannot bypass the developer-file guard."""
    alias = tmp_path / "developer.env"
    alias.symlink_to(_ROOT / ".env")
    source = DotEnvSettingsSource(Settings, env_file=None)
    assert source._read_env_file(alias) == {}
