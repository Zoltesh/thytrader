"""Typed application configuration loaded from environment variables."""

from enum import StrEnum
from ipaddress import IPv4Address, IPv6Address
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
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
    api_port: int = Field(default=8200, ge=1, le=65535)
    allow_remote_access: bool = False
    log_level: Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"] = "INFO"
    coinbase_api_key_name: SecretStr | None = None
    coinbase_api_private_key: SecretStr | None = None

    @field_validator("coinbase_api_key_name", mode="before")
    @classmethod
    def normalize_api_key_name(cls, value: object) -> object:
        """Treat an empty environment placeholder as an absent API key name."""
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
        if not self.api_host.is_loopback:
            message = (
                "Protected remote access is not implemented; THYTRADER_API_HOST must be loopback."
            )
            raise ValueError(message)
        credentials = (self.coinbase_api_key_name, self.coinbase_api_private_key)
        if (credentials[0] is None) != (credentials[1] is None):
            message = "Coinbase API key name and private key must be configured together."
            raise ValueError(message)
        return self
