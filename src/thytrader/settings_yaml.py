"""YAML non-secret settings file: load, validate, persist, and hot-reload.

Secrets (Coinbase, database URL, webhook URL, LLM keys) are forbidden in this
file. Process-identity knobs (bind address, dataset root, environment) stay in
``.env`` / Compose and still require a restart.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import logging
import os
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
import yaml

from thytrader.agent_orchestration.models import YoloTier  # noqa: TC001 - Pydantic field type.
from thytrader.config import Settings, parse_yolo_tiers_value
from thytrader.memory.models import NotifyProvider
from thytrader.memory.notify import notification_sender_from_settings

if TYPE_CHECKING:
    from thytrader.memory.models import NotificationRecord
    from thytrader.memory.notify import DeliveryResult, NotificationSender

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


def load_yaml_overlay(path: Path) -> tuple[dict[str, object], bool]:
    """Load one overlay dict. Missing files yield an empty overlay."""
    if not path.is_file():
        return {}, False
    try:
        loaded: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        message = "YAML settings file is not valid YAML."
        raise YamlSettingsError(message) from error
    if loaded is None:
        return {}, True
    if not isinstance(loaded, dict):
        raise YamlSettingsError("YAML settings root must be a mapping.")
    typed = cast("dict[str, object]", loaded)
    return overlay_from_mapping(typed), True


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
    market_data_worker_product_id: str = Field(default="BTC-USD", pattern=r"^[A-Z0-9]{2,20}-USD$")
    execution_worker_interval_seconds: int = Field(default=30, ge=5, le=3_600)
    notify_provider: NotifyProvider = NotifyProvider.NONE

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
        self._mtime: float | None = None
        self._content_hash: str | None = None
        self._yaml_loaded = False
        self._current = self._build(force_overlay=None)

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

    def current(self) -> Settings:
        """Return cached Settings, reloading when mtime or content changes."""
        with self._lock:
            mtime = self._stat_mtime()
            content_hash = self._stat_content_hash()
            if mtime != self._mtime or content_hash != self._content_hash:
                self._current = self._build(force_overlay=None)
            return self._current

    def adopt_process_settings(self, settings: Settings) -> None:
        """Replace the cached Settings without writing YAML.

        Used when Coinbase secrets change in ``.env`` or process memory. Secrets
        never enter the YAML file.
        """
        with self._lock:
            self._current = settings

    def replace(self, write: YamlSettingsWrite) -> Settings:
        """Atomically write YAML, reload, and return the new Settings."""
        with self._lock:
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
                },
                env_file=self._env_file,
            )
            document = document_from_settings(probe)
            reject_secret_keys(document)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp_path.write_text(dump_yaml_document(document), encoding="utf-8")
            tmp_path.replace(self.path)
            self._current = self._build(force_overlay=overlay_from_mapping(document))
            apply_log_level(self._current)
            return self._current

    def _stat_mtime(self) -> float | None:
        """Return the YAML mtime, or None when the file is absent."""
        try:
            return self.path.stat().st_mtime
        except FileNotFoundError:
            return None

    def _stat_content_hash(self) -> str | None:
        """Return a SHA-256 digest of the YAML bytes, or None when absent."""
        if not self.path.is_file():
            return None
        try:
            payload = self.path.read_bytes()
        except OSError:
            return None
        return hashlib.sha256(payload).hexdigest()

    def _build(self, *, force_overlay: dict[str, object] | None) -> Settings:
        """Construct Settings from env plus YAML overlay."""
        if force_overlay is None:
            overlay, loaded = load_yaml_overlay(self.path)
        else:
            overlay, loaded = force_overlay, True
        self._yaml_loaded = loaded
        self._mtime = self._stat_mtime()
        self._content_hash = self._stat_content_hash()
        settings = _settings_from_overlay(overlay, env_file=self._env_file)
        apply_log_level(settings)
        return settings


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


class ReloadingNotificationSender:
    """Rebuild the inner sender when YAML ``notify_provider`` changes."""

    def __init__(self, store: SettingsStore) -> None:
        """Bind one settings store. Webhook URLs stay inside the inner sender."""
        self._store = store
        self._inner: NotificationSender = notification_sender_from_settings(store.current())
        self._provider = self._inner.provider()

    def provider(self) -> NotifyProvider:
        """Return the current provider after a possible YAML reload."""
        return self._refresh().provider()

    async def deliver(self, record: NotificationRecord) -> DeliveryResult:
        """Deliver through the latest configured backend."""
        return await self._refresh().deliver(record)

    def _refresh(self) -> NotificationSender:
        """Swap the inner sender when the YAML notify provider changes."""
        settings = self._store.current()
        if settings.notify_provider is not self._provider:
            self._inner = notification_sender_from_settings(settings)
            self._provider = settings.notify_provider
        return self._inner
