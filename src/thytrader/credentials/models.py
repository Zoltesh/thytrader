"""Write-only Coinbase credential status contracts.

GET responses never include API key names or private-key material. Validation
errors must use these generic strings so FastAPI 422 bodies cannot echo secrets.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

INVALID_CREDENTIALS_PAYLOAD = "Invalid Coinbase credentials payload."
CREDENTIALS_PERSIST_FAILED = "Could not persist Coinbase credentials."
WORKERS_RESTART_DETAIL = (
    "This API process rebuilt Coinbase clients. Compose and native workers still "
    "read secrets at process start; restart them before live or ingest uses the "
    "new keys. Setting credentials does not arm live trading."
)


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class CoinbaseCredentialsStatus(_FrozenModel):
    """Presence flags for Coinbase Advanced Trade credentials.

    Attributes:
        provider: Always ``coinbase``. Extra exchanges are out of scope.
        configured: True when both secrets are present in this API process.
        persisted: True when the dotenv file was written on the last mutation.
        env_file_writable: True when this process can create or replace the file.
        api_hot_reloaded: True when this process rebuilt Coinbase clients.
        workers_require_restart: Always true after a mutation; workers do not hot-reload.
        workers_restart_detail: Operator copy; contains no secret values.
    """

    provider: Literal["coinbase"] = "coinbase"
    configured: bool
    persisted: bool
    env_file_writable: bool
    api_hot_reloaded: bool
    workers_require_restart: bool
    workers_restart_detail: str = Field(min_length=1, max_length=500)
