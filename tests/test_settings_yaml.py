"""YAML non-secret settings overlay, secret rejection, and paper-tier leftover env."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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
    parse_yaml_overlay,
    read_settings_snapshot,
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
    assert not list(tmp_path.glob(".thytrader.yaml.*.tmp"))


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
    assert not list(tmp_path.glob(".thytrader.yaml.*.tmp"))


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


def test_an_undecodable_torn_read_keeps_previous_settings(tmp_path: Path) -> None:
    """Bytes cut through a multibyte character are an invalid file, never an uncaught error."""
    path = tmp_path / "thytrader.yaml"
    path.write_text("# café\nnotify_min_severity: critical\n", encoding="utf-8")
    store = SettingsStore(path, env_file=None)
    path.write_bytes(b"# caf\xc3\nnotify_min_severity: info\n")
    assert store.current().notify_min_severity == "critical"
    assert store.yaml_invalid is True


def test_a_successful_replace_clears_the_invalid_flag(tmp_path: Path) -> None:
    """Repairing the file through set-settings must not leave configuration degraded."""
    path = tmp_path / "thytrader.yaml"
    path.write_text("log_level: INFO\n")
    store = SettingsStore(path, env_file=None)
    path.write_text("log_level: [\n")
    store.current()
    assert store.yaml_invalid is True
    store.replace(YamlSettingsWrite(yolo_enabled=False, log_level="WARNING"))
    assert store.yaml_invalid is False
    assert store.current().log_level == "WARNING"


def test_published_settings_match_the_fingerprint_of_the_same_bytes(tmp_path: Path) -> None:
    """Settings and the reload fingerprint come from one read, so a later write always reloads."""
    path = tmp_path / "thytrader.yaml"
    path.write_text("notify_min_severity: warning\n")
    store = SettingsStore(path, env_file=None)
    other = SettingsStore(path, env_file=None)
    other.replace(YamlSettingsWrite(yolo_enabled=False, notify_min_severity="critical"))
    assert store.current().notify_min_severity == "critical"
    snapshot = read_settings_snapshot(path)
    assert parse_yaml_overlay(snapshot.payload)["notify_min_severity"] == "critical"


def _flock_unsupported(descriptor: int, operation: int) -> None:
    """``fcntl.flock`` on a filesystem without lock support."""
    del descriptor, operation
    raise OSError(errno.EOPNOTSUPP, os.strerror(errno.EOPNOTSUPP))


def test_settings_work_without_flock_but_refuse_an_unlocked_in_place_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No lock support: reads and atomic writes work; an in-place rewrite is refused."""
    monkeypatch.setattr("thytrader.settings_yaml.fcntl.flock", _flock_unsupported)
    path = tmp_path / "thytrader.yaml"
    path.write_text("log_level: INFO\n")
    store = SettingsStore(path, env_file=None)
    assert store.current().log_level == "INFO"
    store.replace(YamlSettingsWrite(yolo_enabled=False, log_level="WARNING"))
    assert store.current().log_level == "WARNING"
    monkeypatch.setattr(Path, "replace", _refuse_rename(errno.EBUSY))
    with pytest.raises(YamlSettingsError, match="cannot be locked"):
        store.replace(YamlSettingsWrite(yolo_enabled=False, log_level="ERROR"))
    assert load_yaml_overlay(path)[0]["log_level"] == "WARNING"


def test_an_unreadable_reload_keeps_previous_settings_then_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read or lock I/O error during a reload never stops the process."""
    path = tmp_path / "thytrader.yaml"
    path.write_text("log_level: WARNING\n")
    store = SettingsStore(path, env_file=None)

    def unreadable(_: Path) -> None:
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr("thytrader.settings_yaml.read_settings_snapshot", unreadable)
    assert store.current().log_level == "WARNING"
    assert store.yaml_invalid is True
    monkeypatch.undo()
    assert store.current().log_level == "WARNING"
    assert store.yaml_invalid is False


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permission bits")
def test_atomic_replace_needs_no_write_access_to_the_file_itself(tmp_path: Path) -> None:
    """Locking uses a read-only descriptor, so a read-only file in a writable dir still updates."""
    path = tmp_path / "thytrader.yaml"
    path.write_text("log_level: INFO\n")
    path.chmod(0o444)
    store = SettingsStore(path, env_file=None)
    store.replace(YamlSettingsWrite(yolo_enabled=False, log_level="WARNING"))
    assert store.current().log_level == "WARNING"


def test_restoring_rejected_content_reports_the_file_invalid_again(tmp_path: Path) -> None:
    """Bringing back bytes that were rejected before (same mtime) is still reported invalid."""
    path = tmp_path / "thytrader.yaml"
    path.write_text("log_level: INFO\n")
    store = SettingsStore(path, env_file=None)
    path.write_text("log_level: [\n")
    bad = path.stat()
    store.current()
    path.write_text("log_level: WARNING\n")
    assert store.current().log_level == "WARNING"
    assert store.yaml_invalid is False
    path.write_text("log_level: [\n")
    os.utime(path, ns=(bad.st_atime_ns, bad.st_mtime_ns))
    assert store.current().log_level == "WARNING"
    assert store.yaml_invalid is True


def test_concurrent_writers_never_share_a_temporary_file(tmp_path: Path) -> None:
    """Each write publishes its own complete document through a unique temporary file."""
    path = tmp_path / "thytrader.yaml"
    path.write_text("log_level: INFO\n")
    documents = [f"log_level: {level}\n" for level in ("WARNING", "ERROR", "DEBUG")] * 20
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda text: write_settings_file(path, text), documents))
    assert path.read_text() in set(documents)
    assert not list(tmp_path.glob(".thytrader.yaml.*.tmp"))
