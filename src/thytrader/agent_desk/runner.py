"""One desk wake: briefing, harness, answer (ADR 0147).

A per-desk file lock keeps a slow harness from overlapping the next scheduled wake; an overlapped
wake is skipped, not queued. A harness failure is still journaled, as an ``error`` wake. Unless
``always`` is set, ThyTrader is first asked to skip the wake when nothing the model can act on
changed since the newest one (a ``skipped`` wake is journaled and no harness runs).
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
from subprocess import SubprocessError
import sys
from typing import TYPE_CHECKING

from thytrader.agent_desk.client import desk_briefing, record_wake, show_desk, skip_wake
from thytrader.agent_desk.harness import (
    BUILTIN_HARNESSES,
    HarnessError,
    HarnessTemplate,
    custom_template,
    run_harness,
)
from thytrader.agent_http import AgentHttpError

if TYPE_CHECKING:
    from collections.abc import Iterator

DEFAULT_TIMEOUT_SECONDS = 600.0
_HARNESS_NAME = re.compile(r"[a-z][a-z0-9_-]{1,31}")
# A harness that cannot start, crashes, prints undecodable bytes or is misquoted.
_RUN_FAILURES = (HarnessError, OSError, SubprocessError, UnicodeDecodeError, ValueError)


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
    always: bool = False,
) -> dict[str, object]:
    """Run one wake for the desk; returns the journaled wake, or a skip when one is running.

    Without ``always``, an unchanged picture journals a ``skipped`` wake and runs no harness.
    Otherwise the harness and model come from the briefing the model answers (one snapshot with
    its configuration fingerprint), unless overridden; a live desk takes no override. Any
    failure to set up or run the harness is journaled as an ``error`` wake.
    """
    with _desk_lock(desk_id) as held:
        if not held:
            return {"skipped": True, "reason": "DESK_WAKE_RUNNING: another wake holds the lock."}
        started_at = datetime.now(UTC)
        desk = json_object(json_object(show_desk(base_url, desk_id)).get("desk"))
        live = desk.get("mode") == "live"
        refused = _live_refusal(desk, harness=harness, model=model, command=command, live=live)
        selection = _select(desk, harness=harness, model=model, command=command, live=live)
        brief: dict[str, object] = {}
        if selection.template is not None and refused is None and not always:
            verdict = _try_skip(base_url, desk_id, selection.payload(started_at))
            if verdict.get("skipped") is True:
                return json_object(verdict.get("wake"))
            brief = json_object(verdict.get("briefing"))
        if not brief:
            brief = json_object(desk_briefing(base_url, desk_id))
        prompt = brief.get("prompt")
        if not isinstance(prompt, str):
            raise DeskRunError("DESK_BRIEFING_INVALID: the briefing has no prompt.")
        # Answer as the briefing's own configuration says (a PATCH may have landed meanwhile),
        # and re-check a live desk's rules against it: the harness may have become built in.
        refused = refused or _live_refusal(
            brief, harness=harness, model=model, command=command, live=live
        )
        selection = _select(brief, harness=harness, model=model, command=command, live=live)
        if refused is not None:
            # Journaled as a failed wake, so DESK_WAKES_FAILING names the refusal.
            selection = _Selection(
                _label(str(brief.get("harness") or "")), selection.model, None, refused
            )
        payload = selection.payload(started_at) | {
            "briefing_fingerprint": brief.get("fingerprint"),
            "material_fingerprint": brief.get("material_fingerprint") or None,
            "config_fingerprint": brief.get("config_fingerprint") or None,
            "raw_response": "",
        }
        payload |= _answer(selection, prompt=prompt, timeout_seconds=timeout_seconds)
        recorded = json_object(record_wake(base_url, desk_id, payload))
        if not recorded:
            raise DeskRunError("DESK_WAKE_UNRECORDED: the API returned no wake.")
        return recorded


def _try_skip(base_url: str, desk_id: str, payload: dict[str, object]) -> dict[str, object]:
    """Ask ThyTrader to skip; an unanswered request asks the model (never loses a wake)."""
    try:
        return json_object(skip_wake(base_url, desk_id, payload))
    except AgentHttpError:
        return {}


def _answer(selection: _Selection, *, prompt: str, timeout_seconds: float) -> dict[str, object]:
    """The harness's answer, or the failure that stopped it, as wake fields."""
    if selection.template is None:
        return {"harness_error": selection.error}
    try:
        answer = run_harness(
            selection.template,
            prompt=prompt,
            model=selection.model,
            timeout_seconds=timeout_seconds,
        )
    except _RUN_FAILURES as error:
        return {"harness_error": _failure_text(error)}
    return {"raw_response": answer}


@dataclass(frozen=True, slots=True)
class _Selection:
    """The harness a wake runs: its template (None when it cannot be set up) and model."""

    label: str
    model: str | None
    template: HarnessTemplate | None
    error: str = ""

    def payload(self, started_at: datetime) -> dict[str, object]:
        """The wake fields that name who answers."""
        return {
            "harness": self.label,
            "model": self.model,
            "started_at": started_at.isoformat(),
            "runner_fingerprint": runner_fingerprint(self.template, self.model),
        }


def _select(
    source: dict[str, object],
    *,
    harness: str | None,
    model: str | None,
    command: str | None,
    live: bool,
) -> _Selection:
    """Resolve the harness from overrides or ``source`` (a desk or a briefing)."""
    name = harness or str(source.get("harness") or "")
    chosen = model if model is not None else _optional(source.get("model"))
    chosen = None if chosen is None else chosen[:80]
    try:
        template = resolve_template(name, command)
    except (DeskRunError, HarnessError, ValueError) as error:
        return _Selection(_label(name), chosen, None, _failure_text(error))
    # A live desk's answers carry its own harness name (the server checks it); paper ones say
    # ``custom`` for an operator's command.
    custom = command is not None and not live
    return _Selection(template.name if custom else _label(name), chosen, template)


def _live_refusal(
    source: dict[str, object],
    *,
    harness: str | None,
    model: str | None,
    command: str | None,
    live: bool,
) -> str | None:
    """Why a live desk refuses this wake's flags, or None (``source``: a desk or a briefing).

    A live desk runs only its own harness and model; change them with ``update``. Any
    ``--harness`` or ``--model`` flag is refused, even one equal to the desk's now (an update
    may land before the briefing). ``--command`` runs a custom harness (one that is not built
    in) under the desk's own harness name and model; a built-in harness runs as built in.
    """
    builtin = str(source.get("harness") or "") in BUILTIN_HARNESSES
    flagged = harness is not None or model is not None or (command is not None and builtin)
    if not (live and flagged):
        return None
    return (
        "DESK_LIVE_OVERRIDE_REFUSED: a live desk runs its configured harness and model; drop "
        "--harness/--model (and --command for a built-in harness) from the timer, or change "
        "the desk with `thytrader-desk update DESK_ID ... --i-understand-live --confirm`."
    )


def _label(name: str) -> str:
    """A harness name the journal accepts (``unknown`` for anything else)."""
    return name if _HARNESS_NAME.fullmatch(name) else "unknown"


def _failure_text(error: BaseException) -> str:
    """A failure as journaled: its own coded message, or a code naming its type."""
    text = str(error)
    if text.startswith("DESK_"):
        return text[:2000]
    return f"DESK_HARNESS_FAILED: {type(error).__name__}: {text}"[:2000]


def runner_fingerprint(template: HarnessTemplate | None, model: str | None) -> str | None:
    """The digest of what answers a wake: harness name, command template and model."""
    if template is None:
        return None
    body = [template.name, list(template.argv), list(template.model_argv), model]
    canonical = json.dumps(body, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def schedule_text(
    desk_id: str,
    *,
    cadence_minutes: int,
    style: str,
    base_url: str | None = None,
    harness_command: str | None = None,
) -> str:
    """A systemd user timer pair or a crontab line that wakes the desk on its cadence.

    The wake runs from the current directory (where ``.env`` and the credentials directory
    resolve) with the current ``PATH`` (so the harness CLI is found), against ``base_url`` and
    with ``harness_command`` when the desk's harness is not built in.
    """
    program = shlex.quote(_desk_program())
    command = f"{program} wake {shlex.quote(desk_id)} --confirm"
    if base_url:
        command += f" --base-url {shlex.quote(base_url)}"
    if harness_command:
        command += f" --command {shlex.quote(harness_command)}"
    workdir = Path.cwd()
    path = os.environ.get("PATH", "/usr/bin:/bin")
    if style == "cron":
        if cadence_minutes > 60 or 60 % cadence_minutes:
            message = (
                "DESK_SCHEDULE_UNSUPPORTED: cron needs a cadence dividing 60, "
                f"not {cadence_minutes}; use the systemd timer."
            )
            raise DeskRunError(message)
        line = f"cd {shlex.quote(str(workdir))} && PATH={shlex.quote(path)} {command}"
        # cron ends the command at an unescaped % and feeds the rest to stdin.
        return f"*/{cadence_minutes} * * * * {line.replace('%', chr(92) + '%')}\n"
    unit = f"thytrader-desk-{_unit_safe(desk_id)}"
    # systemd expands %-specifiers in unit files; %% is a literal percent.
    command = command.replace("%", "%%")
    path = path.replace("%", "%%")
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
    with (directory / f"{_unit_safe(desk_id)}.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _unit_safe(desk_id: str) -> str:
    """The whole desk id, limited to characters a unit or lock file name can carry."""
    return "".join(char for char in desk_id if char.isalnum() or char == "-") or "desk"


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
