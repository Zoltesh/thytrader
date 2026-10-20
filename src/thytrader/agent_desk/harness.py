"""Run one model harness on a desk briefing prompt and return its answer text (ADR 0147).

Briefing in, decision out: the prompt goes in on stdin (or as ``{prompt}`` in the argv), the
answer comes back on stdout, and ThyTrader alone applies it. Built-in templates turn tools off
where the harness allows it. Each run starts in a new empty directory, so no project
``AGENTS.md``/``CLAUDE.md`` is picked up. ThyTrader and venue credentials are removed from the
environment, so the harness cannot act on ThyTrader directly.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import shlex
import subprocess
import tempfile

PROMPT_PLACEHOLDER = "{prompt}"
MODEL_PLACEHOLDER = "{model}"
_SCRUBBED_PREFIXES = ("THYTRADER_", "COINBASE_")
_MAX_STDERR = 400


class HarnessError(RuntimeError):
    """The harness failed, timed out, or answered nothing."""


@dataclass(frozen=True, slots=True)
class HarnessTemplate:
    """An argv with optional ``{model}`` arguments; the prompt goes on stdin unless in argv."""

    name: str
    argv: tuple[str, ...]
    model_argv: tuple[str, ...] = ()
    note: str = ""


BUILTIN_HARNESSES: dict[str, HarnessTemplate] = {
    "claude": HarnessTemplate(
        name="claude",
        argv=(
            "claude",
            "-p",
            "--tools",
            "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--setting-sources",
            "",
            "--output-format",
            "text",
        ),
        model_argv=("--model", MODEL_PLACEHOLDER),
        note="Claude Code print mode with every tool, MCP server and settings source off.",
    ),
    "pi": HarnessTemplate(
        name="pi",
        argv=(
            "pi",
            "-p",
            "--no-tools",
            "--no-session",
            "--no-extensions",
            "--no-skills",
            "--no-context-files",
            "--no-mcp",
        ),
        model_argv=("--model", MODEL_PLACEHOLDER),
        note="Pi print mode with tools, extensions, skills and context files off.",
    ),
    "codex": HarnessTemplate(
        name="codex",
        argv=("codex", "exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only"),
        model_argv=("-m", MODEL_PLACEHOLDER),
        note="Codex exec in a read-only sandbox (Codex cannot turn its tools off).",
    ),
    "hermes": HarnessTemplate(
        name="hermes",
        argv=("hermes", "--ignore-rules", "-z", PROMPT_PLACEHOLDER),
        model_argv=("-m", MODEL_PLACEHOLDER),
        note="Hermes one-shot without rules or memory injection; Hermes keeps its toolsets.",
    ),
}


def harness_argv(template: HarnessTemplate, *, model: str | None) -> tuple[str, ...]:
    """The argv to run, with the model arguments when a model is named."""
    argv = list(template.argv)
    if model:
        model_args = [model if part == MODEL_PLACEHOLDER else part for part in template.model_argv]
        # A prompt placeholder ends its argv, so model arguments go right after the program.
        if PROMPT_PLACEHOLDER in argv:
            argv = [*argv[:1], *model_args, *argv[1:]]
        else:
            argv = [*argv, *model_args]
    return tuple(argv)


def custom_template(command: str) -> HarnessTemplate:
    """A harness from a command line; ``{model}`` and ``{prompt}`` are substituted."""
    argv = tuple(shlex.split(command))
    if not argv:
        raise HarnessError("DESK_HARNESS_INVALID: the harness command is empty.")
    return HarnessTemplate(name="custom", argv=argv)


def run_harness(
    template: HarnessTemplate, *, prompt: str, model: str | None, timeout_seconds: float
) -> str:
    """Run the harness once and return its stdout; raise :class:`HarnessError` otherwise."""
    argv = tuple(
        part.replace(MODEL_PLACEHOLDER, model or "") for part in harness_argv(template, model=model)
    )
    in_argv = PROMPT_PLACEHOLDER in argv
    argv = tuple(prompt if part == PROMPT_PLACEHOLDER else part for part in argv)
    with tempfile.TemporaryDirectory(prefix="thytrader-desk-") as workdir:
        try:
            completed = subprocess.run(  # noqa: S603 - argv list, no shell.
                argv,
                input=None if in_argv else prompt,
                stdin=subprocess.DEVNULL if in_argv else None,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=workdir,
                env=harness_environment(),
                check=False,
            )
        except FileNotFoundError:
            message = f"DESK_HARNESS_NOT_FOUND: {argv[0]!r} is not on PATH."
            raise HarnessError(message) from None
        except subprocess.TimeoutExpired:
            message = f"DESK_HARNESS_TIMEOUT: no answer within {timeout_seconds:g}s."
            raise HarnessError(message) from None
    if completed.returncode != 0:
        tail = completed.stderr.strip()[-_MAX_STDERR:]
        message = f"DESK_HARNESS_EXIT: exit {completed.returncode}: {tail}"
        raise HarnessError(message)
    if not completed.stdout.strip():
        raise HarnessError("DESK_HARNESS_EMPTY: the harness printed nothing.")
    return completed.stdout


def harness_environment() -> dict[str, str]:
    """The caller's environment without ThyTrader or venue credentials."""
    return {
        key: value for key, value in os.environ.items() if not key.startswith(_SCRUBBED_PREFIXES)
    }
