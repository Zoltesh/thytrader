"""Confirmation and live-acknowledgement gates for ``thytrader-runtime`` mutations."""

from __future__ import annotations

from thytrader.agent_orchestration.confirmation import require_mutation_confirmation
from thytrader.agent_orchestration.models import YoloTier
from thytrader.runtime_control.client import RuntimeControlError

_RUNTIME_CONFIRM_MESSAGE = (
    "Pass --confirm to change paper or live runtimes, the risk-policy "
    "registry, or YAML settings. Live start, live resume, and live place-order "
    "also require --i-understand-live."
)
_CREDENTIALS_CONFIRM_MESSAGE = (
    "Pass --confirm to set or clear Coinbase credentials. YOLO never covers this command."
)


def _require_confirm(
    confirm: bool,
    *,
    base_url: str,
    command: str,
    hard_gate: bool = False,
    tier: YoloTier = YoloTier.PAPER,
    missing_message: str = _RUNTIME_CONFIRM_MESSAGE,
) -> None:
    """Refuse mutations unless `--confirm` is present or YOLO covers the tier."""
    require_mutation_confirmation(
        confirmed=confirm,
        missing_message=missing_message,
        error_type=RuntimeControlError,
        base_url=base_url,
        tier=tier,
        command=command,
        hard_gate=hard_gate,
    )


def _require_live_ack(*, mode: str, acknowledged: bool) -> None:
    """Refuse live arming unless the dedicated live acknowledgement flag is set."""
    if mode == "live" and not acknowledged:
        raise RuntimeControlError("Live trading spends real money. Pass --i-understand-live.")
