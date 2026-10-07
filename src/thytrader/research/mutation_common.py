"""Confirmation gates and argument loaders shared by both ``thytrader-research`` transports.

Mutations need ``--confirm`` (HTTP: or YOLO covering the research tier); ids and documents
are parsed locally before any HTTP call or database session.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from thytrader.agent_orchestration.confirmation import require_mutation_confirmation
from thytrader.agent_orchestration.models import YoloTier
from thytrader.strategies.library import parse_document

if TYPE_CHECKING:
    import argparse

    from thytrader.strategies.library import StrategyDocument

_RESEARCH_CONFIRM_MESSAGE = (
    "Pass --confirm to change research artifacts. "
    "This command cannot deploy, paper-trade, or live-trade."
)
_MUTATIONS = frozenset(
    {
        "create-strategy",
        "save-strategy",
        "import-strategy",
        "clone-strategy",
        "delete-strategy",
        "submit-backtest",
        "submit-study",
        "cancel-research-job",
        "create-campaign",
        "refresh-campaign",
    }
)


class ResearchCliError(RuntimeError):
    """Report a safe operator-facing research command failure."""


def _is_mutation(arguments: argparse.Namespace) -> bool:
    """True when the command changes research artifacts."""
    if arguments.command == "bulk-delete-strategies":
        return not arguments.dry_run
    return arguments.command in _MUTATIONS


def _require_confirm(arguments: argparse.Namespace) -> None:
    """Refuse local mutations unless the operator passed `--confirm`. YOLO is HTTP-only."""
    if _is_mutation(arguments) and not arguments.confirm:
        raise ResearchCliError(_RESEARCH_CONFIRM_MESSAGE)


def _require_http_confirm(arguments: argparse.Namespace, base_url: str) -> None:
    """Refuse HTTP mutations unless `--confirm` is present or YOLO covers research."""
    if not _is_mutation(arguments):
        return
    require_mutation_confirmation(
        confirmed=bool(arguments.confirm),
        missing_message=_RESEARCH_CONFIRM_MESSAGE,
        error_type=ResearchCliError,
        base_url=base_url,
        tier=YoloTier.RESEARCH,
        command=arguments.command,
    )


def _load_json(path_text: str) -> object:
    """Load one UTF-8 JSON document from disk."""
    path = Path(path_text)
    return json.loads(path.read_text(encoding="utf-8"))


def _load_document(path_text: str) -> StrategyDocument:
    """Load one strategy document object (valid or work in progress)."""
    return parse_document(_load_json(path_text))


def _uuid(value: str | None, flag: str) -> UUID:
    """Parse one required UUID flag."""
    try:
        return UUID(str(value))
    except ValueError as error:
        raise ResearchCliError(f"{flag} must be a UUID.") from error


def _clone_name(arguments: argparse.Namespace) -> str | None:
    """Return the requested clone name, refusing blank or over-long names before any call."""
    name = arguments.name
    if name is None:
        return None
    if not name.strip() or len(name) > 120:
        raise ResearchCliError("--name must be 1-120 characters and not blank.")
    return name


def _optional_uuid(value: str | None, flag: str) -> UUID | None:
    """Parse one optional UUID flag."""
    return None if value is None else _uuid(value, flag)
