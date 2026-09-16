"""Installation credential loading and persistence."""

from __future__ import annotations

import os
from pathlib import Path  # noqa: TC003 - persisted credential paths are runtime values.
import secrets

from pydantic import SecretStr

_TOKEN_FILENAME = ".installation-token"  # noqa: S105 - filename, not a secret.
_TOKEN_BYTES = 32


class InstallationTokenError(RuntimeError):
    """Installation credential could not be loaded or persisted."""


def installation_token_path(credentials_dir: Path) -> Path:
    """Return the durable installation-token path under the credentials directory."""
    return credentials_dir / _TOKEN_FILENAME


def resolve_installation_token(
    *,
    configured: SecretStr | None,
    credentials_dir: Path,
) -> SecretStr:
    """Return the configured token or load/create a durable installation credential.

    When no token is configured, a new random token is written to the credentials
    directory when writable. The token value is never logged.
    """
    if configured is not None and configured.get_secret_value().strip():
        return configured
    path = installation_token_path(credentials_dir)
    if path.is_file():
        loaded = path.read_text(encoding="utf-8").strip()
        if loaded:
            return SecretStr(loaded)
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    _persist_token(path, token)
    return SecretStr(token)


def read_installation_token_from_env() -> SecretStr | None:
    """Read ``THYTRADER_INSTALLATION_TOKEN`` when explicitly set."""
    raw = os.environ.get("THYTRADER_INSTALLATION_TOKEN", "").strip()
    if not raw:
        return None
    return SecretStr(raw)


def _persist_token(path: Path, token: str) -> None:
    """Atomically write the installation token with mode 0o600."""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(token + "\n", encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(path)
        path.chmod(0o600)
    except OSError as error:
        raise InstallationTokenError("Could not persist installation token.") from error
