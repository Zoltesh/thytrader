"""YAML non-secret settings overlay, secret rejection, and paper-tier leftover env."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from thytrader.agent_orchestration.models import YoloTier
from thytrader.agent_orchestration.service import orchestration_status
from thytrader.settings_yaml import (
    SettingsStore,
    YamlSettingsError,
    YamlSettingsWrite,
    dump_yaml_document,
    load_yaml_overlay,
    overlay_from_mapping,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_yaml_scalar_paper_overlay() -> None:
    """YAML ``yolo.tiers: paper`` is a valid independent tier, not a hierarchy."""
    overlay = overlay_from_mapping({"yolo": {"enabled": True, "tiers": "paper"}})
    assert overlay["yolo_enabled"] is True
    assert overlay["yolo_tiers"] == (YoloTier.PAPER,)


def test_yaml_rejects_secret_keys() -> None:
    """Coinbase, database, and webhook material cannot be smuggled into YAML."""
    with pytest.raises(YamlSettingsError, match="secrets"):
        overlay_from_mapping({"coinbase_api_private_key": "nope"})
    with pytest.raises(YamlSettingsError, match="secrets"):
        overlay_from_mapping({"database_url": "postgresql://x"})
    with pytest.raises(YamlSettingsError, match="secrets"):
        overlay_from_mapping({"notify_webhook_url": "https://example.test"})


def test_yaml_paper_after_start_applies_without_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Writing YAML ``paper`` after boot changes YOLO without constructing a new store."""
    monkeypatch.delenv("THYTRADER_YOLO_ENABLED", raising=False)
    monkeypatch.delenv("THYTRADER_YOLO_TIERS", raising=False)
    path = tmp_path / "thytrader.yaml"
    store = SettingsStore(path, env_file=None)
    assert store.current().yolo_enabled is False
    store.replace(YamlSettingsWrite(yolo_enabled=True, yolo_tiers=(YoloTier.PAPER,)))
    settings = store.current()
    assert settings.yolo_enabled is True
    assert settings.yolo_tiers == (YoloTier.PAPER,)
    status = orchestration_status(settings)
    assert status.allows(YoloTier.PAPER)
    assert not status.allows(YoloTier.LIVE)
    assert status.live_hard_gate is True
    assert status.live_authority is False
    loaded, exists = load_yaml_overlay(path)
    assert exists is True
    assert loaded["yolo_tiers"] == (YoloTier.PAPER,)


def test_yaml_wins_leftover_env_paper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """YAML source of truth wins leftover ``THYTRADER_YOLO_TIERS=paper``."""
    monkeypatch.setenv("THYTRADER_YOLO_ENABLED", "true")
    monkeypatch.setenv("THYTRADER_YOLO_TIERS", "paper")
    path = tmp_path / "thytrader.yaml"
    store = SettingsStore(path, env_file=None)
    store.replace(YamlSettingsWrite(yolo_enabled=True, yolo_tiers=(YoloTier.DATA,)))
    assert store.current().yolo_tiers == (YoloTier.DATA,)


def test_dump_yaml_document_never_includes_secrets() -> None:
    """Canonical YAML text must not grow secret key names."""
    text = dump_yaml_document(
        {
            "yolo": {"enabled": False, "tiers": []},
            "log_level": "INFO",
            "notify_provider": "none",
        }
    )
    assert "coinbase" not in text.lower()
    assert "webhook_url" not in text
    assert "database_url" not in text
    assert "yolo:" in text
