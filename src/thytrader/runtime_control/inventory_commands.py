"""Read-only deployment inventory, summary, and ledger pages for the runtime CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.runtime_control.client import (
    RuntimeControlError,
    list_deployment_fills,
    list_deployment_orders,
    list_deployments,
    show_deployment,
)

if TYPE_CHECKING:
    import argparse


def add_inventory_arguments(parser: argparse.ArgumentParser) -> None:
    """Attach paging flags to the list command."""
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Return one page instead of the complete snapshot. 1..200.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Stable snapshot offset. Requires --limit. Pin as_of by repeating the page.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Explicit complete snapshot. Default list is already complete. "
        "With --limit, page size.",
    )


def add_show_arguments(parser: argparse.ArgumentParser) -> None:
    """Attach the summary/full switch. Summary labels omitted history."""
    parser.add_argument(
        "--detail",
        choices=("summary", "full"),
        default="summary",
        help=(
            "summary (default) omits historical orders and fills and says so in "
            "ledger_omission. full includes them."
        ),
    )


def add_ledger_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
    name: str,
) -> None:
    """Register a read-only orders or fills page command."""
    parser = subparsers.add_parser(
        name,
        parents=[trailing],
        help=f"Read one page of {name} for one deployment. Does not mutate.",
    )
    parser.add_argument("deployment_id", help="Deployment UUID.")
    parser.add_argument("--limit", type=int, default=100, help="Page size 1..500.")
    parser.add_argument("--cursor", default=None, help="next_cursor from the previous page.")


def run_inventory_read(arguments: argparse.Namespace, base_url: str) -> object:
    """Dispatch list, show, orders, or fills."""
    command = arguments.command
    if command == "list":
        return _list(arguments, base_url)
    if command == "show":
        return show_deployment(base_url, arguments.deployment_id, detail=arguments.detail)
    if command == "orders":
        return list_deployment_orders(
            base_url,
            arguments.deployment_id,
            limit=arguments.limit,
            cursor=arguments.cursor,
        )
    return list_deployment_fills(
        base_url,
        arguments.deployment_id,
        limit=arguments.limit,
        cursor=arguments.cursor,
    )


def _list(arguments: argparse.Namespace, base_url: str) -> object:
    """Return a complete snapshot or one honest page."""
    if arguments.offset and arguments.limit is None:
        raise RuntimeControlError("--offset requires --limit.")
    if arguments.limit is not None and not 1 <= arguments.limit <= 200:
        raise RuntimeControlError("--limit must be between 1 and 200.")
    if arguments.offset < 0:
        raise RuntimeControlError("--offset must be zero or positive.")
    complete = arguments.all or arguments.limit is None
    if complete and arguments.offset:
        raise RuntimeControlError("A complete inventory does not take --offset.")
    if complete:
        return list_deployments(base_url, page_size=arguments.limit or 200)
    return _page_with_contract(base_url, limit=arguments.limit, offset=arguments.offset)


def _page_with_contract(base_url: str, *, limit: int, offset: int) -> object:
    """Mark a single page incomplete when the server says more rows exist."""
    payload = list_deployments(base_url, limit=limit, offset=offset)
    if not isinstance(payload, dict):
        raise RuntimeControlError("Deployment inventory response was not an object.")
    has_more = payload.get("has_more")
    if not isinstance(has_more, bool):
        raise RuntimeControlError("Deployment inventory response omitted boolean has_more.")
    body = dict(payload)
    body["complete"] = not has_more
    body["inventory"] = "page"
    return body
