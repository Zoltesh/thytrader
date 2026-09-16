"""Shared `--confirm` gate with optional YOLO skip on advertised tiers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.agent_http import require_matching_ops_contract
from thytrader.agent_orchestration.client import (
    fetch_orchestration_status,
    record_skipped_confirmation,
)
from thytrader.agent_orchestration.models import YoloTier

if TYPE_CHECKING:
    from collections.abc import Callable


def require_mutation_confirmation(
    *,
    confirmed: bool,
    missing_message: str,
    error_type: type[Exception],
    base_url: str | None = None,
    tier: YoloTier | None = None,
    command: str | None = None,
    hard_gate: bool = False,
) -> None:
    """Refuse a mutation unless `--confirm` is present or YOLO covers the tier.

    ``hard_gate`` is for risk-policy writes, live place-order, ``--local``
    research, and memory. Those paths never consult YOLO. Live
    start/pause/resume/stop use ``YoloTier.LIVE`` instead of ``hard_gate``.
    """
    if confirmed:
        return
    if hard_gate or base_url is None or tier is None or command is None:
        raise error_type(missing_message)
    require_matching_ops_contract(base_url)
    status = fetch_orchestration_status(base_url)
    if not status.allows(tier):
        raise error_type(missing_message)
    record_skipped_confirmation(base_url, tier=tier, command=command)


def require_paper_runtime_confirmation(
    *,
    confirmed: bool,
    missing_message: str,
    error_type: type[Exception],
    base_url: str,
    command: str,
    deployment_mode: Callable[[], str],
) -> None:
    """Allow YOLO for paper or live control according to advertised tiers.

    Paper YOLO never covers a live deployment. Live YOLO never covers paper.
    Live start acknowledgement is a separate ``--i-understand-live`` gate.
    """
    if confirmed:
        return
    require_matching_ops_contract(base_url)
    status = fetch_orchestration_status(base_url)
    if not status.allows(YoloTier.PAPER) and not status.allows(YoloTier.LIVE):
        raise error_type(missing_message)
    mode = deployment_mode()
    if mode == "live":
        tier = YoloTier.LIVE
    elif mode == "paper":
        tier = YoloTier.PAPER
    else:
        raise error_type(missing_message)
    if not status.allows(tier):
        raise error_type(missing_message)
    record_skipped_confirmation(base_url, tier=tier, command=command)
