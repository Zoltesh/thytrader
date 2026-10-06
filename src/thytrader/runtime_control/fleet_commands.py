"""Confirmation-gated fleet CLI. YOLO never covers these commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.agent_http import require_matching_ops_contract
from thytrader.runtime_control.client import (
    RuntimeControlError,
    fleet_execute,
    fleet_inhibition,
    fleet_preview,
)

if TYPE_CHECKING:
    import argparse

    from thytrader.config import Settings

FLEET_MUTATIONS = frozenset({"fleet-disarm", "fleet-stop", "fleet-flatten", "fleet-rearm"})
FLEET_READS = frozenset({"fleet-preview", "fleet-status"})
_ACTIONS = {
    "fleet-disarm": "disarm",
    "fleet-stop": "managed_stop",
    "fleet-flatten": "flatten",
    "fleet-rearm": "rearm",
}
_HTTP = {
    "fleet-disarm": "disarm",
    "fleet-stop": "stop",
    "fleet-flatten": "flatten",
    "fleet-rearm": "rearm",
}


def add_fleet_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
    *,
    confirm_help: str,
    live_help: str,
) -> None:
    """Register read-only preview/status and confirmed fleet mutations."""
    preview = subparsers.add_parser(
        "fleet-preview",
        parents=[trailing],
        help="Preview affected ids, revisions, and residual positions. Does not mutate.",
    )
    preview.add_argument("--action", required=True, choices=tuple(_ACTIONS.values()))
    preview.add_argument("--mode", required=True, choices=("paper", "live", "all"))
    subparsers.add_parser(
        "fleet-status",
        parents=[trailing],
        help="Read the durable entry-inhibition latch. Does not mutate.",
    )
    for command, help_text in (
        ("fleet-disarm", "Inhibit new starts and entries. Does not cancel, pause, or flatten."),
        ("fleet-stop", "Record managed shutdown for confirmed ids. Not a flatten."),
        ("fleet-flatten", "Record explicit flatten for confirmed ids. Not implied by disarm."),
        ("fleet-rearm", "Clear entry inhibition. Does not resume books."),
    ):
        parser = subparsers.add_parser(command, parents=[trailing], help=help_text)
        parser.add_argument("--mode", required=True, choices=("paper", "live", "all"))
        parser.add_argument("--idempotency-key", required=True)
        parser.add_argument("--confirm", action="store_true", help=confirm_help)
        parser.add_argument("--i-understand-live", action="store_true", help=live_help)
        parser.add_argument(
            "--expect",
            action="append",
            default=[],
            help="Confirmed deployment_id:revision from fleet-preview. Repeatable.",
        )
        parser.add_argument(
            "--expect-inhibition",
            action="append",
            default=[],
            help="Confirmed paper:REVISION or live:REVISION from fleet-preview. "
            "Required for every scoped mode on disarm/rearm; repeatable.",
        )
        parser.add_argument(
            "--allow-empty-scope",
            action="store_true",
            help="Allow stop/flatten when the preview listed no books.",
        )


def run_fleet_read(arguments: argparse.Namespace, base_url: str) -> object:
    """Preview or read the latch."""
    require_matching_ops_contract(base_url)
    if arguments.command == "fleet-status":
        return fleet_inhibition(base_url)
    return fleet_preview(base_url, action=arguments.action, mode=arguments.mode)


def run_fleet_mutation(arguments: argparse.Namespace, base_url: str, settings: Settings) -> object:
    """Send one confirmed fleet action. The HTTP body always sets confirm true."""
    if not arguments.confirm:
        raise RuntimeControlError(
            "Pass --confirm for fleet disarm, stop, flatten, or rearm. YOLO never covers them."
        )
    mode = arguments.mode
    action = _ACTIONS[arguments.command]
    if _needs_live_ack(action, mode) and not arguments.i_understand_live:
        raise RuntimeControlError(
            "This fleet action can re-enable or exit live trading. Pass --i-understand-live."
        )
    expects = tuple(_parse_expect(item) for item in arguments.expect)
    if action in {"managed_stop", "flatten"} and not expects and not arguments.allow_empty_scope:
        raise RuntimeControlError(
            "Pass each fleet-preview id as --expect UUID:REVISION, or --allow-empty-scope."
        )
    inhibition = _parse_inhibition(arguments.expect_inhibition)
    modes = ("paper", "live") if mode == "all" else (mode,)
    if action in {"disarm", "rearm"} and any(
        f"{item}_revision" not in inhibition for item in modes
    ):
        raise RuntimeControlError("Pass --expect-inhibition MODE:REVISION for each previewed mode.")
    require_matching_ops_contract(base_url)
    payload: dict[str, object] = {
        "mode": mode,
        "confirm": True,
        "idempotency_key": arguments.idempotency_key,
        "i_understand_live": bool(arguments.i_understand_live),
        "expected_targets": [
            {"deployment_id": deployment_id, "revision": revision}
            for deployment_id, revision in expects
        ],
        "allow_empty_scope": bool(arguments.allow_empty_scope),
        "expected_inhibition": inhibition,
    }
    return fleet_execute(base_url, _HTTP[arguments.command], payload, settings=settings)


def _needs_live_ack(action: str, mode: str) -> bool:
    """Live rearm and live-capable flatten require the explicit acknowledgement."""
    includes_live = mode in {"live", "all"}
    return includes_live and action in {"rearm", "flatten"}


def _parse_inhibition(values: list[str]) -> dict[str, int]:
    """Parse explicit latch revision confirmations without fetching newer consent."""
    revisions: dict[str, int] = {}
    for value in values:
        mode, separator, revision = value.partition(":")
        if separator != ":" or mode not in {"paper", "live"} or not revision.isdecimal():
            raise RuntimeControlError(
                "--expect-inhibition must be paper:REVISION or live:REVISION."
            )
        key = f"{mode}_revision"
        if key in revisions:
            raise RuntimeControlError("Duplicate --expect-inhibition mode.")
        revisions[key] = int(revision)
    return revisions


def _parse_expect(value: str) -> tuple[str, int]:
    """Parse one deployment_id:revision confirmation."""
    deployment_id, separator, revision = value.partition(":")
    if separator != ":" or not deployment_id or not revision.isdecimal():
        raise RuntimeControlError("--expect must be deployment_id:revision.")
    return deployment_id, int(revision)
