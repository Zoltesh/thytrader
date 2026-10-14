"""YAML non-secret settings file: load, validate, persist, and hot-reload.

Secrets (Coinbase, database URL, webhook URL, LLM keys) are forbidden in this
file. Process-identity knobs (bind address, dataset root, environment) stay in
``.env`` / Compose and still require a restart.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import errno
import fcntl
import hashlib
import logging
import os
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any, BinaryIO, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
import yaml

from thytrader.config import (
    NotifyMinSeverity,
    NotifyProvider,
    Settings,
    YoloTier,
    parse_yolo_tiers_value,
)
from thytrader.market_data.products import SPOT_PRODUCT_ID_PATTERN

LOGGER = logging.getLogger(__name__)
DEFAULT_SETTINGS_FILENAME = "thytrader.yaml"
SETTINGS_FILE_ENV = "THYTRADER_SETTINGS_FILE"
YAML_HEADER = (
    "# ThyTrader non-secret settings. Secrets stay in ignored .env.\n"
    "# Coinbase keys, database URLs, webhook URLs, and LLM keys must not appear here.\n"
    "# YOLO, log level, intervals, notify provider, and the market-data default product\n"
    "# apply without restarting API or workers. Bind address, port, environment, and\n"
    "# dataset root stay env-at-boot and still need a restart.\n"
)
YAML_SCALAR_FIELDS: frozenset[str] = frozenset(
    {
        "log_level",
        "snapshot_interval_seconds",
        "market_data_worker_interval_seconds",
        "market_data_worker_lookback_hours",
        "market_data_worker_product_id",
        "execution_worker_interval_seconds",
        "notify_provider",
        "notify_min_severity",
    }
)
SECRET_KEY_TOKENS: frozenset[str] = frozenset(
    {
        "password",
        "secret",
        "token",
        "api_key",
        "private_key",
        "database_url",
        "webhook_url",
        "coinbase",
        "pem",
        "credential",
    }
)
RESTART_REQUIRED_FIELDS: tuple[str, ...] = (
    "environment",
    "api_host",
    "api_port",
    "containerized",
    "allow_remote_access",
    "market_data_dataset_root",
)

if TYPE_CHECKING:
    from collections.abc import Iterator
LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]


class YamlSettingsError(ValueError):
    """The YAML document is invalid, contains secrets, or cannot be applied."""


def default_settings_path() -> Path:
    """Return ``THYTRADER_SETTINGS_FILE`` or ``thytrader.yaml`` in the working directory."""
    raw = os.environ.get(SETTINGS_FILE_ENV, "").strip()
    if raw:
        return Path(raw)
    return Path(DEFAULT_SETTINGS_FILENAME)


def _key_looks_secret(key: str) -> bool:
    """True when a mapping key is a credential-shaped name."""
    normalized = key.strip().lower().replace("-", "_")
    if normalized in SECRET_KEY_TOKENS:
        return True
    return any(token in normalized for token in SECRET_KEY_TOKENS)


def reject_secret_keys(value: object, *, path: str = "") -> None:
    """Refuse YAML that smuggles credentials under any key name."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key)
            child_path = f"{path}.{name}" if path else name
            if _key_looks_secret(name):
                message = f"YAML settings must not contain secrets ({child_path})."
                raise YamlSettingsError(message)
            reject_secret_keys(child, path=child_path)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            reject_secret_keys(child, path=f"{path}[{index}]")


def _as_str_object_map(value: object, *, what: str) -> dict[str, object]:
    """Narrow a YAML mapping to ``dict[str, object]`` or fail closed."""
    if not isinstance(value, dict):
        raise YamlSettingsError(f"{what} must be a mapping.")
    return {str(key): child for key, child in value.items()}


def overlay_from_mapping(raw: Mapping[str, object]) -> dict[str, object]:
    """Translate a YAML mapping into ``Settings`` field overlays.

    ``yolo.tiers`` may be the scalar ``paper`` (or another single enum) as well
    as a list. Unknown keys and secret names fail closed.
    """
    reject_secret_keys(raw)
    allowed = {"yolo", *YAML_SCALAR_FIELDS}
    extra = sorted(set(raw) - allowed)
    if extra:
        message = f"Unknown YAML settings keys: {', '.join(extra)}."
        raise YamlSettingsError(message)
    overlay: dict[str, object] = {}
    yolo = raw.get("yolo")
    if yolo is not None:
        yolo_mapping = _as_str_object_map(yolo, what="yolo")
        yolo_extra = sorted(set(yolo_mapping) - {"enabled", "tiers"})
        if yolo_extra:
            message = f"Unknown YAML yolo keys: {', '.join(yolo_extra)}."
            raise YamlSettingsError(message)
        if "enabled" in yolo_mapping:
            overlay["yolo_enabled"] = yolo_mapping["enabled"]
        if "tiers" in yolo_mapping:
            overlay["yolo_tiers"] = parse_yolo_tiers_value(yolo_mapping["tiers"])
    for field_name in YAML_SCALAR_FIELDS:
        if field_name in raw:
            overlay[field_name] = raw[field_name]
    return overlay


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    """One consistent read of the settings file: bytes, mtime and digest of those bytes."""

    exists: bool
    payload: bytes = b""
    mtime: float | None = None
    digest: str | None = None

    @property
    def fingerprint(self) -> tuple[float | None, str | None]:
        """Identity used to decide whether a reload is needed."""
        return self.mtime, self.digest


def read_settings_snapshot(path: Path) -> FileSnapshot:
    """Read the file once under a shared lock (ADR 0139).

    ThyTrader writers hold an exclusive ``flock`` while rewriting a bind-mounted file in
    place, so readers never observe a half-written document from them. The digest and mtime
    describe exactly the bytes returned.
    """
    try:
        with path.open("rb") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
                return _snapshot_from(handle)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except FileNotFoundError, IsADirectoryError:
        return FileSnapshot(exists=False)


def _snapshot_from(handle: BinaryIO) -> FileSnapshot:
    """Snapshot an already locked handle from its start."""
    handle.seek(0)
    payload = handle.read()
    mtime = os.fstat(handle.fileno()).st_mtime
    return FileSnapshot(
        exists=True, payload=payload, mtime=mtime, digest=hashlib.sha256(payload).hexdigest()
    )


def parse_yaml_overlay(payload: bytes) -> dict[str, object]:
    """Parse settings bytes; undecodable, invalid or non-mapping content is a settings error."""
    try:
        loaded: object = yaml.safe_load(payload.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as error:
        message = "YAML settings file is not valid UTF-8 YAML."
        raise YamlSettingsError(message) from error
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise YamlSettingsError("YAML settings root must be a mapping.")
    typed = cast("dict[str, object]", loaded)
    return overlay_from_mapping(typed)


def load_yaml_overlay(path: Path) -> tuple[dict[str, object], bool]:
    """Load one overlay dict. Missing files yield an empty overlay."""
    snapshot = read_settings_snapshot(path)
    if not snapshot.exists:
        return {}, False
    return parse_yaml_overlay(snapshot.payload), True


def document_from_settings(settings: Settings) -> dict[str, object]:
    """Build the canonical YAML mapping from validated Settings."""
    return {
        "yolo": {
            "enabled": settings.yolo_enabled,
            "tiers": [tier.value for tier in settings.yolo_tiers],
        },
        "log_level": settings.log_level,
        "snapshot_interval_seconds": settings.snapshot_interval_seconds,
        "market_data_worker_interval_seconds": settings.market_data_worker_interval_seconds,
        "market_data_worker_lookback_hours": settings.market_data_worker_lookback_hours,
        "market_data_worker_product_id": settings.market_data_worker_product_id,
        "execution_worker_interval_seconds": settings.execution_worker_interval_seconds,
        "notify_provider": settings.notify_provider.value,
        "notify_min_severity": settings.notify_min_severity,
    }


def dump_yaml_document(document: Mapping[str, object]) -> str:
    """Serialize one canonical YAML document with the committed header."""
    body = yaml.safe_dump(
        dict(document),
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    return YAML_HEADER + "\n" + body


def apply_log_level(settings: Settings) -> None:
    """Apply a hot-reloaded log level to the process root logger."""
    logging.getLogger().setLevel(settings.log_level)


class ProcessSettingsView(BaseModel):
    """Redacted env-at-boot knobs that still require a process restart."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: str
    api_host: str
    api_port: int
    containerized: bool
    allow_remote_access: bool
    market_data_dataset_root: str
    database_configured: bool
    coinbase_credentials_configured: bool
    notify_webhook_configured: bool
    restart_required: Literal[True] = True
    restart_required_fields: tuple[str, ...] = RESTART_REQUIRED_FIELDS


class YamlSettingsWrite(BaseModel):
    """Loopback mutation of YAML non-secrets. Never includes credentials."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    yolo_enabled: bool
    yolo_tiers: tuple[YoloTier, ...] = ()
    log_level: LogLevel = "INFO"
    snapshot_interval_seconds: int = Field(default=300, ge=60, le=86_400)
    market_data_worker_interval_seconds: int = Field(default=300, ge=60, le=86_400)
    market_data_worker_lookback_hours: int = Field(default=168, ge=1, le=2_160)
    market_data_worker_product_id: str = Field(default="BTC-USD", pattern=SPOT_PRODUCT_ID_PATTERN)
    execution_worker_interval_seconds: int = Field(default=30, ge=5, le=3_600)
    notify_provider: NotifyProvider = NotifyProvider.NONE
    # Omitted means keep the current value, so clients that predate it never reset it.
    notify_min_severity: NotifyMinSeverity | None = None

    @field_validator("yolo_tiers", mode="before")
    @classmethod
    def parse_tiers(cls, value: object) -> object:
        """Accept a scalar ``paper`` enum as well as a list."""
        return parse_yolo_tiers_value(value)


class YamlSettingsView(YamlSettingsWrite):
    """GET payload: YAML knobs plus redacted process identity. No secret echo."""

    settings_file: str
    yaml_loaded: bool
    live_hard_gate: Literal[True] = True
    playbook_live_authority: Literal[False] = False
    process: ProcessSettingsView


def view_from_settings(
    settings: Settings,
    *,
    settings_file: Path,
    yaml_loaded: bool,
) -> YamlSettingsView:
    """Project Settings into the loopback settings contract without secrets."""
    return YamlSettingsView(
        yolo_enabled=settings.yolo_enabled,
        yolo_tiers=settings.yolo_tiers,
        log_level=settings.log_level,
        snapshot_interval_seconds=settings.snapshot_interval_seconds,
        market_data_worker_interval_seconds=settings.market_data_worker_interval_seconds,
        market_data_worker_lookback_hours=settings.market_data_worker_lookback_hours,
        market_data_worker_product_id=settings.market_data_worker_product_id,
        execution_worker_interval_seconds=settings.execution_worker_interval_seconds,
        notify_provider=settings.notify_provider,
        notify_min_severity=settings.notify_min_severity,
        settings_file=str(settings_file),
        yaml_loaded=yaml_loaded,
        process=ProcessSettingsView(
            environment=settings.environment.value,
            api_host=str(settings.api_host),
            api_port=settings.api_port,
            containerized=settings.containerized,
            allow_remote_access=settings.allow_remote_access,
            market_data_dataset_root=str(settings.market_data_dataset_root),
            database_configured=settings.database_url is not None,
            coinbase_credentials_configured=(
                settings.coinbase_api_key_name is not None
                and settings.coinbase_api_private_key is not None
            ),
            notify_webhook_configured=settings.notify_webhook_url is not None,
        ),
    )


class SettingsStore:
    """Re-read a YAML settings file when its mtime changes.

    Env secrets and restart-required bind knobs still come from ``Settings``
    environment sources. YAML overlays non-secrets and wins when both are set.
    """

    def __init__(self, path: Path, *, env_file: Path | str | None = ".env") -> None:
        """Bind one YAML path. ``env_file`` matches ``Settings`` dotenv loading."""
        self.path = path
        self._env_file = env_file
        self._lock = Lock()
        self._fingerprint: tuple[float | None, str | None] = (None, None)
        self._yaml_loaded = False
        self._yaml_invalid = False
        self._current = self._publish(read_settings_snapshot(path))

    @classmethod
    def open(
        cls,
        path: Path | None = None,
        *,
        env_file: Path | str | None = ".env",
    ) -> SettingsStore:
        """Open the process settings file (default ``thytrader.yaml``)."""
        return cls(path or default_settings_path(), env_file=env_file)

    @property
    def yaml_loaded(self) -> bool:
        """True when the YAML file exists and was parsed on the last reload."""
        return self._yaml_loaded

    @property
    def yaml_invalid(self) -> bool:
        """True while the file on disk fails to load and the last valid settings are kept."""
        return self._yaml_invalid

    def current(self) -> Settings:
        """Return cached Settings, reloading when mtime or content changes."""
        with self._lock:
            self._refresh_locked()
            return self._current

    def _refresh_locked(self) -> None:
        """Reload a changed file; keep the last valid settings when it does not load.

        A hand edit can still be saved non-atomically or with a typo. Neither may stop a
        running process: the previous settings stay in force, the failure is logged once per
        file content and reported by ``yaml_invalid``, and the next change is retried. Only
        construction fails loudly.
        """
        self._refresh_from(read_settings_snapshot(self.path))

    def _refresh_from(self, snapshot: FileSnapshot) -> None:
        """Apply one snapshot unless it is the one already published."""
        if snapshot.fingerprint == self._fingerprint:
            return
        try:
            self._current = self._publish(snapshot)
        except YamlSettingsError:
            self._fingerprint = snapshot.fingerprint
            self._yaml_invalid = True
            LOGGER.warning("settings_yaml_invalid_keeping_previous path=%s", self.path)

    def _publish(self, snapshot: FileSnapshot) -> Settings:
        """Validate one snapshot, then publish its settings and fingerprint together."""
        overlay = parse_yaml_overlay(snapshot.payload) if snapshot.exists else {}
        settings = _settings_from_overlay(overlay, env_file=self._env_file)
        apply_log_level(settings)
        self._yaml_loaded = snapshot.exists
        self._yaml_invalid = False
        self._fingerprint = snapshot.fingerprint
        return settings

    def adopt_process_settings(self, settings: Settings) -> None:
        """Replace the cached Settings without writing YAML.

        Used when Coinbase secrets change in ``.env`` or process memory. Secrets
        never enter the YAML file.
        """
        with self._lock:
            self._current = settings

    def replace(self, write: YamlSettingsWrite) -> Settings:
        """Atomically write YAML, reload, and return the new Settings.

        The on-disk file is reloaded first, so fields a client omits keep their current
        value even when another process changed the file since this one last read it.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, _exclusive_settings_file(self.path) as handle:
            self._refresh_from(_snapshot_from(handle))
            probe = _settings_from_overlay(
                {
                    "yolo_enabled": write.yolo_enabled,
                    "yolo_tiers": write.yolo_tiers,
                    "log_level": write.log_level,
                    "snapshot_interval_seconds": write.snapshot_interval_seconds,
                    "market_data_worker_interval_seconds": (
                        write.market_data_worker_interval_seconds
                    ),
                    "market_data_worker_lookback_hours": write.market_data_worker_lookback_hours,
                    "market_data_worker_product_id": write.market_data_worker_product_id,
                    "execution_worker_interval_seconds": (write.execution_worker_interval_seconds),
                    "notify_provider": write.notify_provider,
                    "notify_min_severity": (
                        write.notify_min_severity or self._current.notify_min_severity
                    ),
                },
                env_file=self._env_file,
            )
            document = document_from_settings(probe)
            reject_secret_keys(document)
            write_settings_file(self.path, dump_yaml_document(document), locked=handle)
            self._current = self._publish(read_after_write(self.path, handle))
            return self._current


@contextmanager
def _exclusive_settings_file(path: Path) -> Iterator[BinaryIO]:
    """Hold an exclusive ``flock`` on the current settings inode for read-merge-write.

    Every process (API and workers, host or container) sees the same bind-mounted inode, so
    this serializes ThyTrader writers and blocks shared-lock readers during an in-place
    rewrite. If another writer atomically replaced the file while this one waited, the lock
    is retaken on the new inode.
    """
    while True:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        handle = os.fdopen(descriptor, "r+b")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            if os.fstat(handle.fileno()).st_ino != path.stat().st_ino:
                continue
            yield handle
            return
        finally:
            handle.close()


def read_after_write(path: Path, handle: BinaryIO) -> FileSnapshot:
    """Snapshot what was just written: the locked handle when rewritten in place, else the path."""
    try:
        same_inode = os.fstat(handle.fileno()).st_ino == path.stat().st_ino
    except FileNotFoundError:
        same_inode = False
    return _snapshot_from(handle) if same_inode else read_settings_snapshot(path)


def write_settings_file(path: Path, text: str, *, locked: BinaryIO | None = None) -> None:
    """Atomically replace the YAML, or rewrite it in place when the path is a mount point.

    Compose bind-mounts ``thytrader.yaml`` as a single file, and renaming over a mount point
    fails with ``EBUSY`` (``EXDEV`` across devices). The fallback keeps the mounted inode and
    rewrites it under an exclusive ``flock`` (the caller's ``locked`` handle, or one taken
    here), so ThyTrader readers holding a shared lock never see it half written (ADR 0139).
    """
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    try:
        tmp_path.replace(path)
    except OSError as error:
        tmp_path.unlink(missing_ok=True)
        if error.errno not in {errno.EBUSY, errno.EXDEV}:
            raise
        payload = text.encode("utf-8")
        if locked is not None:
            _rewrite_in_place(locked, payload)
            return
        with path.open("r+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                _rewrite_in_place(handle, payload)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _rewrite_in_place(handle: BinaryIO, payload: bytes) -> None:
    """Replace the content of an exclusively locked handle and make it durable."""
    handle.seek(0)
    handle.write(payload)
    handle.truncate(len(payload))
    handle.flush()
    os.fsync(handle.fileno())


def _settings_from_overlay(
    overlay: dict[str, object],
    *,
    env_file: Path | str | None,
) -> Settings:
    """Build Settings from env plus YAML kwargs. YAML/kwargs win leftover env.

    ``overlay`` is untrusted YAML already restricted to known keys. ``Any`` is the
    dynamic BaseSettings ``**kwargs`` boundary; ``Settings`` validates immediately.
    """
    unpacked: dict[str, Any] = dict(overlay)
    try:
        return Settings(_env_file=env_file, **unpacked)
    except ValidationError as error:
        message = "YAML settings are invalid against process secrets and bind knobs."
        raise YamlSettingsError(message) from error
