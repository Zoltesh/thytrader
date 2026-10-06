"""Provider-neutral, redacted evidence for failed read-only exchange requests."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ExchangeReadOperation(StrEnum):
    """Account read operations whose failures operators can distinguish."""

    BALANCES = "balances"
    PERMISSIONS = "permissions"
    PRICE = "price"
    FEES = "fees"
    OPEN_ORDERS = "open_orders"


class ExchangeReadFailureKind(StrEnum):
    """Safe failure categories that never contain provider response text."""

    HTTP = "http"
    TIMEOUT = "timeout"
    NETWORK = "network"
    INVALID_RESPONSE = "invalid_response"
    UNSUPPORTED = "unsupported"


class ExchangeReadFailure(BaseModel):
    """Validated read failure evidence without URLs, bodies, or account identifiers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: ExchangeReadOperation
    kind: ExchangeReadFailureKind
    http_status: int | None = Field(default=None, ge=100, le=599)
    attempts: int = Field(default=1, ge=1, le=2)

    def summary(self) -> str:
        """Render safe operation and category for health component details."""
        status = "" if self.http_status is None else f" HTTP {self.http_status}"
        return (
            f"Exchange {self.operation.value} read failed: {self.kind.value}{status} "
            f"after {self.attempts} attempt(s)."
        )


class ExchangeReadError(OSError):
    """Fail a read without returning partial evidence or raw transport exceptions."""

    def __init__(self, failure: ExchangeReadFailure) -> None:
        """Carry validated public evidence and preserve transport-error handling."""
        self.failure = failure
        super().__init__(failure.summary())
