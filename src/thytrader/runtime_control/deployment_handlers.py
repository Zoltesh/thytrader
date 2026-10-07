"""``thytrader-runtime`` handlers for commands that act on one deployment.

Start, place-order, pause/resume/stop, reset-breaker-latches, the decision timeline,
and twin metadata. Ids are validated and gates checked before any HTTP mutation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from thytrader.agent_http import require_matching_ops_contract
from thytrader.agent_orchestration.confirmation import require_paper_runtime_confirmation
from thytrader.agent_orchestration.models import YoloTier
from thytrader.execution.decisions import DECISION_PAGE_MAX_LIMIT
from thytrader.runtime_control.client import (
    RuntimeControlError,
    link_twin,
    list_decisions,
    place_discretionary_order,
    reset_breaker_latches,
    set_deployment_status,
    show_deployment,
    show_twin,
    start_deployment,
    unlink_twin,
)
from thytrader.runtime_control.gates import (
    _RUNTIME_CONFIRM_MESSAGE,
    _require_confirm,
    _require_live_ack,
)

if TYPE_CHECKING:
    import argparse

    from thytrader.config import Settings


def _deployment_mode(payload: object) -> str:
    """Read mode from a deployment snapshot without leaking extra fields."""
    if isinstance(payload, dict):
        mode = payload.get("mode")
        if isinstance(mode, str):
            return mode
    raise RuntimeControlError("Deployment snapshot omitted mode.")


def _start(arguments: argparse.Namespace, base_url: str, settings: Settings) -> object:
    """Start paper or live; live still requires `--i-understand-live`."""
    live = arguments.mode == "live"
    _require_live_ack(mode=arguments.mode, acknowledged=arguments.i_understand_live)
    _require_confirm(
        arguments.confirm,
        base_url=base_url,
        command="start",
        tier=YoloTier.LIVE if live else YoloTier.PAPER,
    )
    cash = _paper_cash(mode=arguments.mode, cash=arguments.cash)
    maker, taker = _paper_fees(
        mode=arguments.mode,
        maker_fee_rate=arguments.maker_fee_rate,
        taker_fee_rate=arguments.taker_fee_rate,
    )
    require_matching_ops_contract(base_url)
    return start_deployment(
        base_url,
        strategy_id=str(_strategy_uuid(arguments.strategy_id)),
        mode=arguments.mode,
        paper_starting_cash=cash,
        maker_fee_rate=maker,
        taker_fee_rate=taker,
        settings=settings,
        i_understand_live=live and arguments.i_understand_live,
    )


def _decisions(arguments: argparse.Namespace, base_url: str) -> object:
    """Read one decision page; validates ids locally before any HTTP call."""
    deployment_id = arguments.deployment_id
    strategy_id = arguments.strategy_id
    if deployment_id is None and strategy_id is None:
        raise RuntimeControlError("Pass a deployment id or --strategy-id.")
    if deployment_id is not None:
        deployment_id = str(_uuid_argument(deployment_id, "deployment id"))
    if strategy_id is not None:
        strategy_id = str(_strategy_uuid(strategy_id))
    if not 1 <= arguments.limit <= DECISION_PAGE_MAX_LIMIT:
        raise RuntimeControlError(f"--limit must be between 1 and {DECISION_PAGE_MAX_LIMIT}.")
    require_matching_ops_contract(base_url)
    return list_decisions(
        base_url,
        deployment_id=deployment_id,
        strategy_id=strategy_id,
        outcomes=tuple(arguments.outcome or ()),
        limit=arguments.limit,
        cursor=arguments.cursor,
    )


def _uuid_argument(value: str, label: str) -> UUID:
    """Parse one UUID argument before any HTTP call."""
    try:
        return UUID(value)
    except ValueError as error:
        raise RuntimeControlError(f"The {label} must be a UUID.") from error


def _strategy_uuid(value: str) -> UUID:
    """Parse --strategy-id before any HTTP call."""
    try:
        return UUID(value)
    except ValueError as error:
        raise RuntimeControlError("--strategy-id must be a strategy UUID.") from error


def _place_order(arguments: argparse.Namespace, base_url: str, settings: Settings) -> object:
    """Place one paper (YOLO-eligible) or live (confirm hard-gated) discretionary order."""
    live = arguments.mode == "live"
    _require_live_ack(mode=arguments.mode, acknowledged=arguments.i_understand_live)
    _require_confirm(
        arguments.confirm,
        base_url=base_url,
        command="place-order",
        hard_gate=live,
        tier=YoloTier.PAPER,
    )
    cash = _paper_cash(mode=arguments.mode, cash=arguments.cash)
    maker, taker = _paper_fees(
        mode=arguments.mode,
        maker_fee_rate=arguments.maker_fee_rate,
        taker_fee_rate=arguments.taker_fee_rate,
    )
    require_matching_ops_contract(base_url)
    return place_discretionary_order(
        base_url,
        settings=settings,
        mode=arguments.mode,
        product_id=arguments.product_id,
        stop_price=arguments.stop_price,
        take_profit_price=arguments.take_profit_price,
        idempotency_key=arguments.idempotency_key,
        origin=arguments.origin,
        entry_kind=arguments.entry_kind,
        timeframe=arguments.timeframe,
        side=arguments.side,
        quantity=arguments.quantity,
        quote_notional=arguments.quote_notional,
        limit_price=arguments.limit_price,
        paper_starting_cash=cash,
        maker_fee_rate=maker,
        taker_fee_rate=taker,
        note=arguments.note,
        i_understand_live=live and arguments.i_understand_live,
    )


def _runtime_mutation(arguments: argparse.Namespace, base_url: str, settings: Settings) -> object:
    """Pause, resume, stop, or reset breaker latches on one deployment."""
    command = arguments.command
    if command == "reset-breaker-latches":
        _require_confirm(
            arguments.confirm,
            base_url=base_url,
            command=command,
            hard_gate=True,
        )
        require_matching_ops_contract(base_url)
        return reset_breaker_latches(
            base_url,
            arguments.deployment_id,
            settings=settings,
        )
    acknowledged = bool(getattr(arguments, "i_understand_live", False))
    live_resume = False
    if command == "resume":
        mode = _deployment_mode(show_deployment(base_url, arguments.deployment_id))
        _require_live_ack(mode=mode, acknowledged=acknowledged)
        live_resume = mode == "live"
    require_paper_runtime_confirmation(
        confirmed=arguments.confirm,
        missing_message=_RUNTIME_CONFIRM_MESSAGE,
        error_type=RuntimeControlError,
        base_url=base_url,
        command=command,
        deployment_mode=lambda: _deployment_mode(
            show_deployment(base_url, arguments.deployment_id)
        ),
    )
    require_matching_ops_contract(base_url)
    return set_deployment_status(
        base_url,
        arguments.deployment_id,
        command,
        settings=settings,
        flatten=bool(getattr(arguments, "flatten", False)),
        i_understand_live=live_resume and acknowledged,
    )


def _paper_cash(*, mode: str, cash: str | None) -> str | None:
    """Require paper cash and reject it for live start."""
    if mode == "live":
        if cash is not None:
            raise RuntimeControlError("Live start does not accept --cash.")
        return None
    if cash is None:
        raise RuntimeControlError("Paper requires --cash.")
    return cash


def _paper_fees(
    *, mode: str, maker_fee_rate: str | None, taker_fee_rate: str | None
) -> tuple[str | None, str | None]:
    """Accept optional paper maker/taker assumptions and reject them for live."""
    if mode == "live":
        if maker_fee_rate is not None or taker_fee_rate is not None:
            raise RuntimeControlError("Live start does not accept paper fee rates.")
        return None, None
    if (maker_fee_rate is None) != (taker_fee_rate is None):
        raise RuntimeControlError(
            "Paper fee rates require both --maker-fee-rate and --taker-fee-rate."
        )
    return maker_fee_rate, taker_fee_rate


def _twin_command(arguments: argparse.Namespace, base_url: str, settings: Settings) -> object:
    """Validate ids and gate metadata mutations before any HTTP request."""
    deployment_id = str(_uuid_argument(arguments.deployment_id, "Deployment id"))
    if arguments.command == "show-twin":
        require_matching_ops_contract(base_url)
        return show_twin(base_url, deployment_id)
    counterpart_id = str(
        _uuid_argument(arguments.counterpart_deployment_id, "Counterpart deployment id")
    )
    _require_confirm(
        arguments.confirm, base_url=base_url, command=arguments.command, hard_gate=True
    )
    require_matching_ops_contract(base_url)
    helper = link_twin if arguments.command == "link-twin" else unlink_twin
    return helper(base_url, deployment_id, counterpart_id, settings=settings)
