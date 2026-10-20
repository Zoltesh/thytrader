"""One desk wake: briefing, harness, answer (ADR 0147).

A per-desk file lock keeps a slow harness from overlapping the next scheduled wake; an overlapped
wake is skipped, not queued. A harness failure is still journaled, as an ``error`` wake.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import fcntl
import os
from pathlib import Path
import shlex
import sys
from typing import TYPE_CHECKING

from thytrader.agent_desk.client import desk_briefing, record_wake
from thytrader.agent_desk.harness import (
    BUILTIN_HARNESSES,
    HarnessError,
    HarnessTemplate,
    custom_template,
    run_harness,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

DEFAULT_TIMEOUT_SECONDS = 600.0


class DeskRunError(RuntimeError):
    """The wake could not start (unknown harness, unreadable briefing)."""


def resolve_template(harness: str, command: str | None) -> HarnessTemplate:
    """A built-in template by name, or the operator's own ``--command``."""
    if command:
        return custom_template(command)
    template = BUILTIN_HARNESSES.get(harness)
    if template is None:
        names = ", ".join(sorted(BUILTIN_HARNESSES))
        message = (
            f"DESK_HARNESS_UNKNOWN: no built-in harness {harness!r} ({names}); "
            "pass --command for any other harness."
        )
        raise DeskRunError(message)
    return template


def wake(
    base_url: str,
    desk_id: str,
    *,
    harness: str | None,
    model: str | None,
    command: str | None,
    timeout_seconds: float,
) -> dict[str, object]:
    """Run one wake for the desk; returns the journaled wake, or a skip when one is running."""
    with _desk_lock(desk_id) as held:
        if not held:
            return {"skipped": True, "reason": "DESK_WAKE_RUNNING: another wake holds the lock."}
        started_at = datetime.now(UTC)
        brief = json_object(desk_briefing(base_url, desk_id))
        prompt = brief.get("prompt")
        if not isinstance(prompt, str):
            raise DeskRunError("DESK_BRIEFING_INVALID: the briefing has no prompt.")
        desk = json_object(brief.get("desk"))
        harness_name = harness or str(desk.get("harness") or "")
        model_name = model if model is not None else _optional(desk.get("model"))
        template = resolve_template(harness_name, command)
        payload: dict[str, object] = {
            "harness": template.name if command else harness_name,
            "model": model_name,
            "briefing_fingerprint": brief.get("fingerprint"),
            "started_at": started_at.isoformat(),
        }
        try:
            answer = run_harness(
                template, prompt=prompt, model=model_name, timeout_seconds=timeout_seconds
            )
        except HarnessError as error:
            payload |= {"raw_response": "", "harness_error": str(error)}
        else:
            payload["raw_response"] = answer
        recorded = json_object(record_wake(base_url, desk_id, payload))
        if not recorded:
            raise DeskRunError("DESK_WAKE_UNRECORDED: the API returned no wake.")
        return recorded


def schedule_text(desk_id: str, *, cadence_minutes: int, style: str) -> str:
    """A systemd user timer pair or a crontab line that wakes the desk on its cadence.

    The wake runs from the current directory (where ``.env`` and the credentials directory
    resolve) with the current ``PATH`` (so the harness CLI is found).
    """
    program = shlex.quote(_desk_program())
    command = f"{program} wake {shlex.quote(desk_id)} --confirm"
    workdir = Path.cwd()
    path = os.environ.get("PATH", "/usr/bin:/bin")
    if style == "cron":
        if cadence_minutes > 60 or 60 % cadence_minutes:
            message = (
                "DESK_SCHEDULE_UNSUPPORTED: cron needs a cadence dividing 60, "
                f"not {cadence_minutes}; use the systemd timer."
            )
            raise DeskRunError(message)
        return (
            f"*/{cadence_minutes} * * * * cd {shlex.quote(str(workdir))} && "
            f"PATH={shlex.quote(path)} {command}\n"
        )
    unit = f"thytrader-desk-{desk_id[:8]}"
    return (
        f"# ~/.config/systemd/user/{unit}.service\n"
        "[Unit]\n"
        f"Description=ThyTrader agent desk wake {desk_id}\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"WorkingDirectory={workdir}\n"
        f"Environment=PATH={path}\n"
        f"ExecStart={command}\n\n"
        f"# ~/.config/systemd/user/{unit}.timer\n"
        "[Unit]\n"
        f"Description=Wake ThyTrader agent desk {desk_id} every {cadence_minutes} minutes\n\n"
        "[Timer]\n"
        "OnBootSec=5min\n"
        f"OnUnitActiveSec={cadence_minutes}min\n"
        "Persistent=true\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n\n"
        f"# then: systemctl --user daemon-reload && systemctl --user enable --now {unit}.timer\n"
    )


@contextmanager
def _desk_lock(desk_id: str) -> Iterator[bool]:
    """Hold the desk's wake lock if it is free; yields whether it was taken."""
    directory = _state_directory()
    directory.mkdir(parents=True, exist_ok=True)
    safe = "".join(char for char in desk_id if char.isalnum() or char == "-") or "desk"
    with (directory / f"{safe}.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _state_directory() -> Path:
    """Where wake locks live (``$XDG_STATE_HOME/thytrader/desks``)."""
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "thytrader" / "desks"


def _desk_program() -> str:
    """The installed ``thytrader-desk`` next to this interpreter, or the bare name."""
    candidate = Path(sys.executable).with_name("thytrader-desk")
    return str(candidate) if candidate.exists() else "thytrader-desk"


def json_object(value: object) -> dict[str, object]:
    """A JSON object with string keys, or an empty one for anything else."""
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _optional(value: object) -> str | None:
    """A non-empty string or None."""
    return value if isinstance(value, str) and value else None
