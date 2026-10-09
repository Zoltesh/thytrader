"""``thytrader-runtime`` inventory adoption commands (ADR 0124), live only.

- ``adoption-preview`` is read-only. It reports the venue balance, every live book's
  claims, the adoptable quantity, the mark, and what blocks protect and sell.
- ``place-order --entry-kind adopt`` adopts held coins into a discretionary book that
  rests the given stop and take-profit.
- ``sell-holdings`` adopts them into a stopped FLATTEN book that the worker sells.

Both mutations always need ``--confirm`` (YOLO never covers them) and
``--i-understand-live``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlencode

from thytrader.agent_http import request_json, request_mutation_json, require_matching_ops_contract
from thytrader.market_data.models import EXECUTION_TIMEFRAMES
from thytrader.runtime_control.client import RuntimeControlError
from thytrader.runtime_control.gates import _require_confirm, _require_live_ack

if TYPE_CHECKING:
    import argparse

    from thytrader.config import Settings

ADOPTION_COMMANDS = frozenset({"adoption-preview", "sell-holdings"})
_ADOPT_REJECTED_FLAGS = (
    ("quote_notional", "--quote-notional"),
    ("limit_price", "--limit-price"),
    ("cash", "--cash"),
    ("maker_fee_rate", "--maker-fee-rate"),
    ("taker_fee_rate", "--taker-fee-rate"),
)


def add_adoption_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
    *,
    confirm_help: str,
    live_help: str,
) -> None:
    """Register ``adoption-preview`` (read-only) and ``sell-holdings`` (live mutation)."""
    preview = subparsers.add_parser(
        "adoption-preview",
        parents=[trailing],
        help=(
            "Read-only: the Coinbase balance of a product's base coin, what live books "
            "already claim of it, how much is adoptable, the mark, and what blocks "
            "protect (place-order --entry-kind adopt) or sell-holdings. Writes nothing."
        ),
    )
    preview.add_argument("--product-id", required=True, help="Spot product, e.g. DOGE-USDC.")
    _timeframe_argument(preview)
    sell = subparsers.add_parser(
        "sell-holdings",
        parents=[trailing],
        help=(
            "LIVE: adopt unmanaged held coins into a stopped book flagged FLATTEN; the "
            "execution worker then sells them marketably into the product's quote "
            "currency. No protective order is placed. Not entry-gated by the risk policy "
            "(it reduces risk) and allowed under fleet disarm."
        ),
    )
    sell.add_argument("--product-id", required=True, help="Spot product to sell into.")
    sell.add_argument(
        "--quantity",
        required=True,
        help="Base quantity (rounded down to the base increment), or 'all' unmanaged.",
    )
    sell.add_argument(
        "--idempotency-key",
        required=True,
        help="Unique key; repeating it returns the original book instead of selling twice.",
    )
    sell.add_argument("--origin", default="agent", choices=("human", "agent"))
    _timeframe_argument(sell)
    sell.add_argument("--note", default=None, help="Optional why-note on the trade reason.")
    sell.add_argument("--confirm", action="store_true", help=confirm_help)
    sell.add_argument("--i-understand-live", action="store_true", help=live_help)


def _timeframe_argument(parser: argparse.ArgumentParser) -> None:
    """The book clock whose last closed candle is the mark."""
    parser.add_argument(
        "--timeframe",
        default="5m",
        choices=EXECUTION_TIMEFRAMES,
        help="Book clock; its last closed candle's close is the mark (default 5m).",
    )


def run_adoption_command(
    arguments: argparse.Namespace, base_url: str, settings: Settings
) -> object:
    """Preview, or sell after the confirm and live gates."""
    if arguments.command == "adoption-preview":
        require_matching_ops_contract(base_url)
        return adoption_preview(
            base_url, product_id=arguments.product_id, timeframe=arguments.timeframe
        )
    _live_gates(arguments, base_url, command="sell-holdings")
    require_matching_ops_contract(base_url)
    return post_inventory_adoption(
        base_url,
        settings=settings,
        payload=_payload(arguments, action="sell"),
    )


def adopt_via_place_order(
    arguments: argparse.Namespace, base_url: str, settings: Settings
) -> object:
    """``place-order --entry-kind adopt``: protect held coins with the given SL/TP."""
    if arguments.mode != "live":
        raise RuntimeControlError(
            "ADOPTION_LIVE_ONLY: --entry-kind adopt adopts coins held at Coinbase; "
            "pass --mode live."
        )
    if arguments.side != "long":
        raise RuntimeControlError("--entry-kind adopt adopts held coins as a long only.")
    if arguments.quantity is None:
        raise RuntimeControlError("--entry-kind adopt requires --quantity N or --quantity all.")
    for attribute, flag in _ADOPT_REJECTED_FLAGS:
        if getattr(arguments, attribute) is not None:
            raise RuntimeControlError(f"--entry-kind adopt does not take {flag}.")
    _live_gates(arguments, base_url, command="place-order")
    require_matching_ops_contract(base_url)
    payload = _payload(arguments, action="protect")
    payload["stop_price"] = arguments.stop_price
    payload["take_profit_price"] = arguments.take_profit_price
    return post_inventory_adoption(base_url, settings=settings, payload=payload)


def _live_gates(arguments: argparse.Namespace, base_url: str, *, command: str) -> None:
    """Live acknowledgement first, then the confirmation YOLO never skips."""
    _require_live_ack(mode="live", acknowledged=arguments.i_understand_live)
    _require_confirm(arguments.confirm, base_url=base_url, command=command, hard_gate=True)


def _payload(arguments: argparse.Namespace, *, action: str) -> dict[str, str | bool]:
    """The shared HTTP body of protect and sell."""
    payload: dict[str, str | bool] = {
        "mode": "live",
        "action": action,
        "product_id": arguments.product_id,
        "quantity": arguments.quantity,
        "idempotency_key": arguments.idempotency_key,
        "origin": arguments.origin,
        "timeframe": arguments.timeframe,
        "i_understand_live": True,
    }
    if arguments.note is not None:
        payload["note"] = arguments.note
    return payload


def adoption_preview(base_url: str, *, product_id: str, timeframe: str) -> object:
    """GET the read-only adoption preview."""
    query = urlencode({"product_id": product_id, "timeframe": timeframe})
    return request_json(method="GET", url=f"{base_url}/api/v1/inventory-adoptions/preview?{query}")


def post_inventory_adoption(
    base_url: str, *, settings: Settings | None, payload: dict[str, str | bool]
) -> object:
    """POST one live adoption with installation auth."""
    return request_mutation_json(
        method="POST",
        url=f"{base_url}/api/v1/inventory-adoptions",
        payload=payload,
        settings=settings,
    )
