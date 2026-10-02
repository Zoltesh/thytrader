"""Name what failed when an agent CLI hits an error it did not anticipate.

Agent CLIs used to collapse every unexpected exception into a fixed "... failed
safely" line, which hid schema drift, unreadable input files, and transport
faults behind the same words. ``describe_unexpected_failure`` keeps the safety
statement but says what failed and what to do next. Messages pass through the
operator redaction helpers, so configured secrets, PEM blocks, and URL
passwords never reach agent output.
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from thytrader.config import Settings
from thytrader.operator.redaction import configured_secrets, redact_text

_MAX_DETAIL_CHARS = 300
_REPEAT_HINT = "If it repeats, run `uv run thytrader-operator health` and report this message."


def describe_unexpected_failure(error: BaseException, *, lane: str, safety: str) -> str:
    """Return one line: which lane failed, what failed, the safety statement, the next step.

    Args:
        error: The exception the CLI did not map to a lane-specific message.
        lane: Human name of the command family, such as ``"Research command"``.
        safety: What is guaranteed not to have changed, such as
            ``"Paper and live state were not changed."``.
    """
    what, next_step = _classify(error)
    message = f"{lane} failed: {what}. {safety} {next_step}"
    return redact_text(message, _secrets())


def _classify(error: BaseException) -> tuple[str, str]:
    """Map one exception to a short cause and the action that fixes it."""
    if isinstance(error, ValidationError):
        return (
            f"a payload did not match this CLI's schema ({_first_issue(error)})",
            "If the API was rebuilt from a different checkout, compare ops contracts with "
            "`uv run thytrader-operator health` and rebuild with `make run`.",
        )
    if isinstance(error, json.JSONDecodeError):
        return (
            f"input is not valid JSON ({error.msg} at line {error.lineno} column {error.colno})",
            "Fix the JSON document and re-run the command.",
        )
    if isinstance(error, UnicodeDecodeError):
        return ("input is not UTF-8 text", "Save the file as UTF-8 and re-run the command.")
    if isinstance(error, OSError) and error.filename is not None:
        reason = error.strerror or type(error).__name__
        return (
            f"could not read {error.filename} ({reason})",
            "Check the path (relative paths resolve from the current directory) and re-run.",
        )
    return (f"unexpected {type(error).__name__}: {_detail(error)}", _REPEAT_HINT)


def _first_issue(error: ValidationError) -> str:
    """Render the first Pydantic issue as ``location: message``."""
    issues = error.errors()
    if not issues:
        return "validation failed"
    first = issues[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    message = str(first.get("msg", "invalid value"))
    remaining = len(issues) - 1
    suffix = f"; {remaining} more issue(s)" if remaining > 0 else ""
    rendered = f"{location}: {message}" if location else message
    return f"{_shorten(rendered)}{suffix}"


def _detail(error: BaseException) -> str:
    """Return the first line of one exception message, shortened."""
    text = str(error).strip().splitlines()
    return _shorten(text[0]) if text else "no detail"


def _shorten(text: str) -> str:
    """Cap one detail so unexpected messages cannot flood agent output."""
    if len(text) <= _MAX_DETAIL_CHARS:
        return text
    return f"{text[: _MAX_DETAIL_CHARS - 1]}…"


def _secrets() -> tuple[str, ...]:
    """Collect configured secrets for redaction, or none when settings cannot load.

    The failure being described may itself be a settings problem, so loading
    settings here must never raise over the original error.
    """
    try:
        return configured_secrets(Settings())
    except Exception:  # noqa: BLE001 - redaction must not mask the error it reports.
        return ()
