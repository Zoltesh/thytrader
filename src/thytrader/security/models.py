"""Trust-boundary models for installation auth and CSRF."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

INSTALLATION_AUTH_HEADER = "Authorization"
CSRF_HEADER = "X-CSRF-Token"
CSRF_COOKIE = "thytrader_csrf"


class SecuritySessionView(BaseModel):
    """Browser session metadata returned after installation auth."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    csrf_token: str
    live_requires_published_policy: Literal[True] = True


class TrustBoundaryStatusView(BaseModel):
    """Advertised trust-boundary configuration for operators and agents."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    host_validation: bool
    origin_validation: bool
    csrf_required_for_browser: bool
    installation_token_configured: bool
    credentials_env_file: str
    shared_credentials_volume: bool
