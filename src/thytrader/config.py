"""Typed application configuration loaded from environment variables."""

from enum import StrEnum
from ipaddress import IPv4Address, IPv6Address
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Supported ThyTrader runtime environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


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
    api_port: int = Field(default=8000, ge=1, le=65535)
    allow_remote_access: bool = False
    log_level: Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"] = "INFO"
    coinbase_api_key_name: SecretStr | None = None
    coinbase_api_private_key: SecretStr | None = None

    @model_validator(mode="after")
    def validate_network_binding(self) -> Self:
        """Reject accidental non-loopback exposure unless explicitly allowed."""
        if not self.api_host.is_loopback and not self.allow_remote_access:
            message = "Non-loopback API binding requires THYTRADER_ALLOW_REMOTE_ACCESS=true."
            raise ValueError(message)
        return self
