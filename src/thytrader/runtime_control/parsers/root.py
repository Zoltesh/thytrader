"""The ``thytrader-runtime`` top-level parser, assembled from the subcommand groups."""

from __future__ import annotations

import argparse

from thytrader.agent_http import base_url_options
from thytrader.cli_parse import trailing_options
from thytrader.runtime_control.adoption_commands import add_adoption_parsers
from thytrader.runtime_control.fleet_commands import add_fleet_parsers
from thytrader.runtime_control.inventory_commands import add_ledger_parser
from thytrader.runtime_control.parsers.common import _CONFIRM_HELP, _LIVE_HELP
from thytrader.runtime_control.parsers.configuration import (
    add_credential_parsers,
    add_risk_policy_parsers,
    add_settings_parsers,
)
from thytrader.runtime_control.parsers.deployments import (
    _add_decisions_parser,
    _add_twin_parsers,
    add_deployment_action_parsers,
    add_inventory_read_parsers,
    add_place_order_parser,
    add_start_parser,
)
from thytrader.runtime_control.portfolio_commands import add_portfolio_parsers


def _parser() -> argparse.ArgumentParser:
    """Build the confirmation-gated runtime-control argument parser."""
    shared = base_url_options()
    trailing = trailing_options(shared)
    parser = argparse.ArgumentParser(
        prog="thytrader-runtime",
        description=(
            "Start, pause, resume, or stop paper and live deployments and whole "
            "portfolios (portfolio-*), read their "
            "per-bar decision timeline (decisions, read-only), place "
            "discretionary orders, adopt or sell coins already held at Coinbase "
            "(adoption-preview, place-order --entry-kind adopt, sell-holdings; live only), "
            "publish the risk-policy registry, update YAML "
            "non-secret settings (including YOLO), and set or clear write-only "
            "Coinbase credentials, through the loopback HTTP API. Mutations "
            "require --confirm unless YOLO covers that tier. Live start, live "
            "resume, live place-order, and sell-holdings also require --i-understand-live. "
            "Live place-order, sell-holdings, link-twin, unlink-twin, set-risk-policy, "
            "set-settings, and Coinbase "
            "credential set/clear "
            "never skip --confirm. This is not the operator or research CLI."
        ),
        parents=[shared],
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_inventory_read_parsers(subparsers, trailing)
    add_ledger_parser(subparsers, trailing, "orders")
    add_ledger_parser(subparsers, trailing, "fills")
    add_fleet_parsers(subparsers, trailing, confirm_help=_CONFIRM_HELP, live_help=_LIVE_HELP)
    _add_decisions_parser(subparsers, trailing)
    _add_twin_parsers(subparsers, trailing)
    add_start_parser(subparsers, trailing)
    add_place_order_parser(subparsers, trailing)
    add_adoption_parsers(subparsers, trailing, confirm_help=_CONFIRM_HELP, live_help=_LIVE_HELP)
    add_deployment_action_parsers(subparsers, trailing)
    add_risk_policy_parsers(subparsers, trailing)
    add_settings_parsers(subparsers, trailing)
    add_credential_parsers(subparsers, trailing)
    add_portfolio_parsers(subparsers, trailing, confirm_help=_CONFIRM_HELP, live_help=_LIVE_HELP)
    return parser
