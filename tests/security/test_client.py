"""CLI installation-auth resolution against cross-environment trust boundaries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import SecretStr

if TYPE_CHECKING:
    from pathlib import Path

from thytrader.config import Environment, Settings
from thytrader.security.client import mutation_headers, resolve_installation_token_for_client
from thytrader.security.installation import installation_token_path
from thytrader.security.models import INSTALLATION_AUTH_HEADER


def test_resolve_token_when_local_boundary_disabled_but_file_exists(tmp_path: Path) -> None:
    """Host CLIs in development must still send tokens the production API expects."""
    credentials_dir = tmp_path / "credentials"
    credentials_dir.mkdir()
    token_path = installation_token_path(credentials_dir)
    token_path.write_text("cross-env-installation-token\n", encoding="utf-8")

    settings = Settings(
        environment=Environment.DEVELOPMENT,
        trust_boundary_enabled=False,
        credentials_dir=credentials_dir,
        _env_file=None,
    )

    assert resolve_installation_token_for_client(settings) == "cross-env-installation-token"
    headers = mutation_headers(settings)
    assert headers[INSTALLATION_AUTH_HEADER] == "Bearer cross-env-installation-token"


def test_resolve_token_absent_when_local_boundary_disabled_and_no_file(tmp_path: Path) -> None:
    """Development CLIs without a token stay unauthenticated for open local APIs."""
    settings = Settings(
        environment=Environment.DEVELOPMENT,
        trust_boundary_enabled=False,
        credentials_dir=tmp_path / "empty",
        _env_file=None,
    )

    assert resolve_installation_token_for_client(settings) is None
    assert mutation_headers(settings) == {}


def test_resolve_token_prefers_explicit_env_over_file(tmp_path: Path) -> None:
    """THYTRADER_INSTALLATION_TOKEN wins over the credentials-directory file."""
    credentials_dir = tmp_path / "credentials"
    credentials_dir.mkdir()
    installation_token_path(credentials_dir).write_text("file-token\n", encoding="utf-8")

    settings = Settings(
        environment=Environment.DEVELOPMENT,
        trust_boundary_enabled=False,
        credentials_dir=credentials_dir,
        installation_token=SecretStr("env-token"),
        _env_file=None,
    )

    assert resolve_installation_token_for_client(settings) == "env-token"
