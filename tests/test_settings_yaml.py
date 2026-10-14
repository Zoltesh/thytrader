"""YAML non-secret settings overlay, secret rejection, and paper-tier leftover env."""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

from thytrader.agent_orchestration.service import orchestration_status
from thytrader.config import YoloTier
from thytrader.settings_yaml import (
    SettingsStore,
    YamlSettingsError,
    YamlSettingsWrite,
    dump_yaml_document,
    load_yaml_overlay,
    overlay_from_mapping,
    reject_secret_keys,
    write_settings_file,
)

if TYPE_CHECKING:
    from collections.abc import Callable


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
    """Canonical YAML mapping must not grow secret keys; comments may warn about them."""
    text = dump_yaml_document(
        {
            "yolo": {"enabled": False, "tiers": []},
            "log_level": "INFO",
            "notify_provider": "none",
        }
    )
    loaded = yaml.safe_load(text)
    assert isinstance(loaded, dict)
    reject_secret_keys(loaded)
    assert "yolo:" in text


def _refuse_rename(code: int) -> Callable[..., Path]:
    """Path.replace stand-in that fails like a rename over a bind-mounted file."""

    def refuse(self: Path, target: Path) -> Path:
        del self, target
        raise OSError(code, os.strerror(code))

    return refuse


def test_settings_write_rewrites_a_bind_mounted_file_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compose mounts thytrader.yaml as one file; EBUSY falls back to the same inode."""
    path = tmp_path / "thytrader.yaml"
    path.write_text("notify_provider: none\nlog_level: INFO\n# a longer original tail\n")
    inode = path.stat().st_ino
    monkeypatch.setattr(Path, "replace", _refuse_rename(errno.EBUSY))
    write_settings_file(path, "notify_provider: webhook\n")
    assert path.read_text() == "notify_provider: webhook\n"
    assert path.stat().st_ino == inode
    assert not (tmp_path / "thytrader.yaml.tmp").exists()


def test_settings_write_does_not_hide_other_rename_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only mount-point errors fall back; anything else still fails loudly."""
    path = tmp_path / "thytrader.yaml"
    path.write_text("notify_provider: none\n")
    monkeypatch.setattr(Path, "replace", _refuse_rename(errno.EACCES))
    with pytest.raises(PermissionError):
        write_settings_file(path, "notify_provider: log\n")
    assert path.read_text() == "notify_provider: none\n"
    assert not (tmp_path / "thytrader.yaml.tmp").exists()


def test_settings_store_replace_works_on_a_bind_mounted_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``set-settings`` over HTTP used to return 500 in Compose; the store now writes in place."""
    path = tmp_path / "thytrader.yaml"
    path.write_text("log_level: INFO\n")
    store = SettingsStore(path, env_file=None)
    monkeypatch.setattr(Path, "replace", _refuse_rename(errno.EBUSY))
    settings = store.replace(YamlSettingsWrite(yolo_enabled=False, log_level="WARNING"))
    assert settings.log_level == "WARNING"
    assert load_yaml_overlay(path)[0]["log_level"] == "WARNING"


def test_settings_reload_keeps_previous_values_when_the_file_does_not_load(
    tmp_path: Path,
) -> None:
    """A torn in-place write or a typo never stops a running process (ADR 0139)."""
    path = tmp_path / "thytrader.yaml"
    path.write_text("notify_min_severity: critical\n")
    store = SettingsStore(path, env_file=None)
    assert store.current().notify_min_severity == "critical"
    path.write_text("notify_min_severity: info\ncal\n  bad: [\n")
    assert store.current().notify_min_severity == "critical"
    assert store.yaml_invalid is True
    path.write_text("notify_min_severity: warning\n")
    assert store.current().notify_min_severity == "warning"
    assert store.yaml_invalid is False


def test_settings_store_still_refuses_an_invalid_file_at_construction(tmp_path: Path) -> None:
    """With no previous settings to keep, an unloadable file stays a startup error."""
    path = tmp_path / "thytrader.yaml"
    path.write_text("log_level: [\n")
    with pytest.raises(YamlSettingsError):
        SettingsStore(path, env_file=None)


def test_settings_replace_merges_omitted_fields_from_the_file_on_disk(tmp_path: Path) -> None:
    """An older client omitting the minimum keeps another process's newer on-disk value."""
    path = tmp_path / "thytrader.yaml"
    path.write_text("notify_min_severity: info\n")
    api = SettingsStore(path, env_file=None)
    assert api.current().notify_min_severity == "info"
    SettingsStore(path, env_file=None).replace(
        YamlSettingsWrite(yolo_enabled=False, notify_min_severity="critical")
    )
    written = api.replace(YamlSettingsWrite(yolo_enabled=False, log_level="WARNING"))
    assert written.notify_min_severity == "critical"
    assert load_yaml_overlay(path)[0]["notify_min_severity"] == "critical"
