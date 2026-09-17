"""CLI helpers for installation auth on trust-boundary mutations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.security.installation import (
    installation_token_path,
    read_installation_token_from_env,
)
from thytrader.security.models import CSRF_HEADER, INSTALLATION_AUTH_HEADER

if TYPE_CHECKING:
    from thytrader.config import Settings


def resolve_installation_token_for_client(settings: Settings) -> str | None:
    """Return the installation token for agent HTTP mutation calls when one is configured.

    Agent CLIs often run with ``environment=development`` while the loopback API runs
    ``production`` under Compose. Local ``trust_boundary_enabled`` therefore stays false
    even when the API enforces installation auth. Send the Bearer token whenever it can
    be resolved from env or the credentials directory; omit it only when absent.
    """
    configured = settings.installation_token or read_installation_token_from_env()
    if configured is not None:
        return configured.get_secret_value()
    path = installation_token_path(settings.credentials_dir)
    if path.is_file():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    return None


def mutation_headers(
    settings: Settings,
    *,
    csrf_token: str | None = None,
) -> dict[str, str]:
    """Build trust-boundary headers for one mutation request."""
    headers: dict[str, str] = {}
    token = resolve_installation_token_for_client(settings)
    if token is not None:
        headers[INSTALLATION_AUTH_HEADER] = f"Bearer {token}"
    if csrf_token is not None:
        headers[CSRF_HEADER] = csrf_token
    return headers
