"""Typed application configuration loaded from environment variables."""

from enum import StrEnum
from ipaddress import IPv4Address, IPv6Address
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from thytrader.agent_orchestration.models import (
    YoloTier,  # noqa: TC001 - Pydantic resolves this annotation at runtime.
)
from thytrader.memory.models import (
    NotifyProvider,
)


class Environment(StrEnum):
    """Supported ThyTrader runtime environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


_COMPOSE_ANY_INTERFACE = IPv4Address("0.0.0.0")  # noqa: S104 - restricted to the Docker network.


class Settings(BaseSettings):
    """Validated server-side settings for ThyTrader processes."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="THYTRADER_",
        extra="ignore",
        frozen=True,
    )

    environment: Environment = Environment.DEVELOPMENT
    api_host: IPv4Address | IPv6Address = IPv4Address("127.0.0.1")
    api_port: int = Field(default=8200, ge=1, le=65535)
    containerized: bool = False
    allow_remote_access: bool = False
    log_level: Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"] = "INFO"
    database_url: SecretStr | None = None
    snapshot_interval_seconds: int = Field(default=300, ge=60, le=86_400)
    worker_readiness_file: Path | None = None
    market_data_worker_interval_seconds: int = Field(default=300, ge=60, le=86_400)
    market_data_worker_lookback_hours: int = Field(default=168, ge=1, le=2_160)
    market_data_worker_product_id: str = Field(default="BTC-USD", pattern=r"^[A-Z0-9]{2,20}-USD$")
    market_data_dataset_root: Path = Path("data/market-data")
    market_data_worker_readiness_file: Path | None = None
    execution_worker_interval_seconds: int = Field(default=30, ge=5, le=3_600)
    execution_worker_readiness_file: Path | None = None
    coinbase_api_key_name: SecretStr | None = None
    coinbase_api_private_key: SecretStr | None = None
    yolo_enabled: bool = False
    yolo_tiers: tuple[YoloTier, ...] = ()
    notify_provider: NotifyProvider = NotifyProvider.NONE
    notify_webhook_url: SecretStr | None = None

    @field_validator("database_url", "coinbase_api_key_name", "notify_webhook_url", mode="before")
    @classmethod
    def normalize_optional_secret(cls, value: object) -> object:
        """Treat empty environment placeholders as absent optional secrets."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("coinbase_api_private_key", mode="before")
    @classmethod
    def expand_private_key_newlines(cls, value: object) -> object:
        """Normalize an empty placeholder and expand escaped PEM newlines."""
        if isinstance(value, str):
            if not value.strip():
                return None
            return value.replace("\\n", "\n")
        return value

    @model_validator(mode="after")
    def validate_network_binding(self) -> Self:
        """Reject unsafe network exposure and incomplete Coinbase credentials."""
        container_listener = self.containerized and self.api_host == _COMPOSE_ANY_INTERFACE
        if not self.api_host.is_loopback and not container_listener:
            message = (
                "Protected remote access is not implemented; THYTRADER_API_HOST must be loopback "
                "unless the process runs in the Compose network."
            )
            raise ValueError(message)
        credentials = (self.coinbase_api_key_name, self.coinbase_api_private_key)
        if (credentials[0] is None) != (credentials[1] is None):
            message = "Coinbase API key name and private key must be configured together."
            raise ValueError(message)
        return self

    @field_validator("yolo_tiers", mode="before")
    @classmethod
    def parse_yolo_tiers(cls, value: object) -> object:
        """Parse comma-separated YOLO tiers, including optional live."""
        if value is None:
            return ()
        if isinstance(value, str):
            return tuple(part.strip().lower() for part in value.split(",") if part.strip())
        return value

    @model_validator(mode="after")
    def validate_yolo_opt_in(self) -> Self:
        """YOLO stays off unless both the flag and a non-empty allowed-tier set are set."""
        unique = tuple(dict.fromkeys(self.yolo_tiers))
        if unique != self.yolo_tiers:
            raise ValueError("THYTRADER_YOLO_TIERS must not contain duplicates.")
        if self.yolo_enabled and not self.yolo_tiers:
            raise ValueError(
                "THYTRADER_YOLO_ENABLED requires THYTRADER_YOLO_TIERS "
                "(data, research, paper, and/or live). Default remains --confirm. "
                "Live YOLO still requires --i-understand-live."
            )
        if self.yolo_tiers and not self.yolo_enabled:
            raise ValueError(
                "THYTRADER_YOLO_TIERS requires THYTRADER_YOLO_ENABLED=true. "
                "Default remains --confirm."
            )
        return self

    @model_validator(mode="after")
    def validate_notify_provider(self) -> Self:
        """Webhook notify requires a URL; other providers must not set one."""
        if self.notify_provider is NotifyProvider.WEBHOOK and self.notify_webhook_url is None:
            raise ValueError(
                "THYTRADER_NOTIFY_PROVIDER=webhook requires THYTRADER_NOTIFY_WEBHOOK_URL."
            )
        if (
            self.notify_provider is not NotifyProvider.WEBHOOK
            and self.notify_webhook_url is not None
        ):
            raise ValueError(
                "THYTRADER_NOTIFY_WEBHOOK_URL requires THYTRADER_NOTIFY_PROVIDER=webhook."
            )
        return self
