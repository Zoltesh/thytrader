"""Loopback HTTP contract for YAML non-secret settings and YOLO tiers."""

from __future__ import annotations

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from thytrader.api.dependencies import get_runtime_state
from thytrader.runtime import RuntimeState  # noqa: TC001 - FastAPI Depends.
from thytrader.settings_yaml import (
    YamlSettingsError,
    YamlSettingsView,
    YamlSettingsWrite,
    default_settings_path,
    view_from_settings,
)

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


def _view(runtime: RuntimeState) -> YamlSettingsView:
    """Project the latest settings without echoing secrets."""
    store = runtime.settings_store
    if store is not None:
        return view_from_settings(
            store.current(),
            settings_file=store.path,
            yaml_loaded=store.yaml_loaded,
        )
    return view_from_settings(
        runtime.settings,
        settings_file=default_settings_path(),
        yaml_loaded=False,
    )


@router.get("", response_model=YamlSettingsView)
async def get_yaml_settings(
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
) -> YamlSettingsView:
    """Return YAML non-secrets plus redacted process identity. No secret echo."""
    return _view(runtime)


@router.put("", response_model=YamlSettingsView)
async def put_yaml_settings(
    body: YamlSettingsWrite,
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
) -> YamlSettingsView:
    """Persist YAML non-secrets and apply YOLO/intervals without a process restart."""
    store = runtime.settings_store
    if store is None:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail={
                "code": "settings_store_unavailable",
                "message": "YAML settings are not attached to this process.",
            },
        )
    try:
        store.replace(body)
    except YamlSettingsError as error:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail={"code": "yaml_settings_invalid", "message": str(error)},
        ) from None
    return _view(runtime)
