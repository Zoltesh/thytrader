"""Persist Coinbase dotenv assignments without logging secret values."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

_KEY_NAME = "THYTRADER_COINBASE_API_KEY_NAME"
_PRIVATE_KEY = "THYTRADER_COINBASE_API_PRIVATE_KEY"
_MANAGED = frozenset({_KEY_NAME, _PRIVATE_KEY})


class CredentialsEnvError(RuntimeError):
    """Env-file persist failed without exposing secret values."""


def quote_env_value(value: str) -> str:
    """Quote a dotenv assignment so PEM newlines survive as escaped sequences."""
    escaped = value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
    return f'"{escaped}"'


def assignment_line(name: str, value: str) -> str:
    """Render one KEY=value line, always quoted."""
    return f"{name}={quote_env_value(value)}\n"


def is_managed_assignment(line: str) -> bool:
    """True when a dotenv line assigns a Coinbase credential variable."""
    stripped = line.strip()
    if stripped.startswith("export "):
        stripped = stripped.removeprefix("export ").lstrip()
    return any(stripped.startswith((f"{name}=", f"{name} =")) for name in _MANAGED)


def env_file_writable(path: Path) -> bool:
    """True when this process can create or replace the dotenv file."""
    parent = path.parent
    if not parent.exists() or not parent.is_dir():
        return False
    if not os.access(parent, os.W_OK | os.X_OK):
        return False
    if path.exists():
        return os.access(path, os.W_OK)
    return True


def upsert_coinbase_env(*, path: Path, key_name: str, private_key: str) -> None:
    """Replace or append Coinbase keys while preserving unrelated lines.

    Empty strings write placeholders so Settings treats credentials as absent.
    """
    existing = _read_existing_lines(path)
    kept = [line for line in existing if not is_managed_assignment(line)]
    body = "".join(kept)
    if body and not body.endswith("\n"):
        body += "\n"
    body += assignment_line(_KEY_NAME, key_name)
    body += assignment_line(_PRIVATE_KEY, private_key)
    _atomic_write(path, body)


def clear_coinbase_env(*, path: Path) -> None:
    """Write empty Coinbase placeholders so Settings treats them as absent."""
    upsert_coinbase_env(path=path, key_name="", private_key="")


def _read_existing_lines(path: Path) -> list[str]:
    """Return existing dotenv lines, or an empty list when the file is absent."""
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise CredentialsEnvError("Could not read the credentials env file.") from error
    if text == "":
        return []
    return text.splitlines(keepends=True)


def _atomic_write(path: Path, body: str) -> None:
    """Write ``body`` via a same-directory replace and set mode 0o600."""
    parent = path.parent
    fd: int | None = None
    tmp_name: str | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=".thytrader-env-", dir=str(parent), text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path = Path(tmp_name)
        tmp_path.chmod(0o600)
        tmp_path.replace(path)
        tmp_name = None
        path.chmod(0o600)
    except OSError as error:
        raise CredentialsEnvError(
            "Could not persist Coinbase credentials to the env file."
        ) from error
    finally:
        if fd is not None:
            os.close(fd)
        if tmp_name is not None:
            Path(tmp_name).unlink(missing_ok=True)
