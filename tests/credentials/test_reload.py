"""Shared Coinbase credential reload store."""

from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr

from thytrader.config import Settings
from thytrader.credentials.reload import CoinbaseCredentialReloadStore
from thytrader.credentials.worker_runtime import WorkerCredentialRuntime
from thytrader.settings_yaml import SettingsStore


def test_reload_store_picks_up_dotenv_changes(tmp_path: Path) -> None:
    """Workers observe credential rotations through content-hash reload."""
    credentials_dir = tmp_path / "credentials"
    credentials_dir.mkdir()
    env_path = credentials_dir / ".env"
    env_path.write_text(
        "THYTRADER_COINBASE_API_KEY_NAME=old-key\n"
        "THYTRADER_COINBASE_API_PRIVATE_KEY=old-secret\n",
        encoding="utf-8",
    )
    base = Settings(credentials_dir=credentials_dir)
    store = CoinbaseCredentialReloadStore(env_path=env_path, base_settings=base)
    assert store.current.coinbase_api_key_name is not None
    assert store.current.coinbase_api_key_name.get_secret_value() == "old-key"

    env_path.write_text(
        "THYTRADER_COINBASE_API_KEY_NAME=new-key\n"
        "THYTRADER_COINBASE_API_PRIVATE_KEY=new-secret\n",
        encoding="utf-8",
    )
    refreshed = store.current
    assert refreshed.coinbase_api_key_name is not None
    assert refreshed.coinbase_api_key_name.get_secret_value() == "new-key"


def test_worker_runtime_merges_yaml_and_dotenv(tmp_path: Path, monkeypatch) -> None:
    """YAML reload keeps non-secret knobs while dotenv supplies Coinbase secrets."""
    credentials_dir = tmp_path / "credentials"
    credentials_dir.mkdir()
    env_path = credentials_dir / ".env"
    env_path.write_text(
        "THYTRADER_COINBASE_API_KEY_NAME=worker-key\n"
        "THYTRADER_COINBASE_API_PRIVATE_KEY=worker-secret\n",
        encoding="utf-8",
    )
    yaml_path = tmp_path / "thytrader.yaml"
    yaml_path.write_text("log_level: INFO\n", encoding="utf-8")
    monkeypatch.setenv("THYTRADER_SETTINGS_FILE", str(yaml_path))
    monkeypatch.setenv("THYTRADER_CREDENTIALS_DIR", str(credentials_dir))

    settings_store = SettingsStore.open()
    runtime = WorkerCredentialRuntime(settings_store)
    current = runtime.current_settings()
    assert current.coinbase_api_key_name is not None
    assert current.coinbase_api_key_name.get_secret_value() == "worker-key"
    assert current.log_level == "INFO"

    yaml_path.write_text("log_level: DEBUG\n", encoding="utf-8")
    updated = runtime.current_settings()
    assert updated.log_level == "DEBUG"
    assert updated.coinbase_api_key_name is not None
    assert updated.coinbase_api_key_name.get_secret_value() == "worker-key"
