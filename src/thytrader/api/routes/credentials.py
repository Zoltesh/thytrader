"""Write-only HTTP contract for Coinbase Advanced Trade credentials.

GET never returns secret values. PUT/DELETE never echo request bodies in 422
payloads. LLM keys are not accepted here.
"""

from __future__ import annotations

from json import JSONDecodeError
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from thytrader.api.dependencies import get_audit_event_store, get_runtime_state
from thytrader.credentials.envfile import (
    CredentialsEnvError,
    clear_coinbase_env,
    env_file_writable,
    upsert_coinbase_env,
)
from thytrader.credentials.models import (
    CREDENTIALS_PERSIST_FAILED,
    INVALID_CREDENTIALS_PAYLOAD,
    CoinbaseCredentialsStatus,
)
from thytrader.credentials.service import coinbase_status, settings_with_coinbase
from thytrader.execution.ids import utc_now
from thytrader.persistence.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventStore,
)
from thytrader.runtime import RuntimeState  # noqa: TC001 - FastAPI Depends.

router = APIRouter(prefix="/api/v1/credentials", tags=["credentials"])

_WRITE_FIELDS = frozenset({"api_key_name", "private_key"})
_MAX_KEY_NAME = 512
_MAX_PRIVATE_KEY = 16_384


def get_credentials_env_file(request: Request) -> Path:
    """Return the dotenv path attached during app construction."""
    path = getattr(request.app.state, "credentials_env_file", None)
    if isinstance(path, Path):
        return path
    return Path(".env")


async def suppress_credentials_validation_echo(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Strip FastAPI 422 ``input`` fields on the credentials route."""
    if request.url.path.startswith("/api/v1/credentials"):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": INVALID_CREDENTIALS_PAYLOAD},
        )
    if not isinstance(exc, RequestValidationError):
        raise exc
    return await request_validation_exception_handler(request, exc)


@router.get("/coinbase", response_model=CoinbaseCredentialsStatus)
async def get_coinbase_credentials(
    request: Request,
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
    env_path: Annotated[Path, Depends(get_credentials_env_file)],
) -> CoinbaseCredentialsStatus:
    """Report whether Coinbase secrets are configured; never echo them."""
    del request
    return coinbase_status(
        runtime.settings,
        env_path=env_path,
        persisted=False,
        api_hot_reloaded=False,
        workers_require_restart=False,
    )


@router.put("/coinbase", response_model=CoinbaseCredentialsStatus)
async def put_coinbase_credentials(
    request: Request,
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
    env_path: Annotated[Path, Depends(get_credentials_env_file)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
) -> CoinbaseCredentialsStatus:
    """Set or rotate Coinbase secrets, persist when writable, and hot-reload."""
    key_name, private_key = await _read_write_pair(request)
    return await _apply_coinbase_mutation(
        request=request,
        runtime=runtime,
        env_path=env_path,
        audit=audit,
        key_name=key_name,
        private_key=private_key,
        action="set_coinbase_credentials",
        detail="Coinbase Advanced Trade credentials were set.",
    )


@router.delete("/coinbase", response_model=CoinbaseCredentialsStatus)
async def delete_coinbase_credentials(
    request: Request,
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
    env_path: Annotated[Path, Depends(get_credentials_env_file)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
) -> CoinbaseCredentialsStatus:
    """Clear Coinbase secrets, persist empty placeholders, and return to demo."""
    return await _apply_coinbase_mutation(
        request=request,
        runtime=runtime,
        env_path=env_path,
        audit=audit,
        key_name=None,
        private_key=None,
        action="clear_coinbase_credentials",
        detail="Coinbase Advanced Trade credentials were cleared.",
    )


async def _read_write_pair(request: Request) -> tuple[str, str]:
    """Parse PUT JSON without putting secret values into exception details."""
    try:
        payload = await request.json()
    except JSONDecodeError, UnicodeDecodeError, ValueError, TypeError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=INVALID_CREDENTIALS_PAYLOAD,
        ) from None
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=INVALID_CREDENTIALS_PAYLOAD,
        )
    extra = set(payload) - _WRITE_FIELDS
    name = payload.get("api_key_name")
    private_key = payload.get("private_key")
    if extra or not isinstance(name, str) or not isinstance(private_key, str):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=INVALID_CREDENTIALS_PAYLOAD,
        )
    name = name.strip()
    private_key = private_key.replace("\\n", "\n").strip()
    too_long = len(name) > _MAX_KEY_NAME or len(private_key) > _MAX_PRIVATE_KEY
    if not name or not private_key or too_long:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=INVALID_CREDENTIALS_PAYLOAD,
        )
    return name, private_key


async def _apply_coinbase_mutation(
    *,
    request: Request,
    runtime: RuntimeState,
    env_path: Path,
    audit: AuditEventStore,
    key_name: str | None,
    private_key: str | None,
    action: str,
    detail: str,
) -> CoinbaseCredentialsStatus:
    """Persist, hot-reload, and audit one set or clear without logging secrets."""
    new_settings = settings_with_coinbase(
        runtime.settings,
        key_name=key_name,
        private_key=private_key,
    )
    persisted = _persist_env(env_path=env_path, key_name=key_name, private_key=private_key)
    applier = getattr(request.app.state, "apply_coinbase_settings", None)
    if not callable(applier):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=CREDENTIALS_PERSIST_FAILED,
        )
    applier(request.app, new_settings)
    await audit.append(
        AuditEvent(
            occurred_at=utc_now(),
            category=AuditEventCategory.RUNTIME,
            action=action,
            outcome=AuditEventOutcome.SUCCESS,
            detail=detail,
        )
    )
    return coinbase_status(
        new_settings,
        env_path=env_path,
        persisted=persisted,
        api_hot_reloaded=True,
        workers_require_restart=False,
    )


def _persist_env(*, env_path: Path, key_name: str | None, private_key: str | None) -> bool:
    """Write or clear dotenv keys when the file is writable.

    Returns:
        True when the file was written. False when skipped because it is not writable.

    Raises:
        HTTPException: 503 when a writable path still fails to persist.
    """
    if not env_file_writable(env_path):
        return False
    try:
        if key_name is None or private_key is None:
            clear_coinbase_env(path=env_path)
        else:
            upsert_coinbase_env(path=env_path, key_name=key_name, private_key=private_key)
    except CredentialsEnvError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=CREDENTIALS_PERSIST_FAILED,
        ) from None
    return True
