"""Confirmation helper: `--confirm` short-circuit, YOLO skip, and live hard gate."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from thytrader.agent_orchestration.confirmation import (
    require_mutation_confirmation,
    require_paper_runtime_confirmation,
)
from thytrader.agent_orchestration.models import (
    AgentOrchestrationStatus,
    ConfirmationMode,
    YoloTier,
)


def test_confirmed_mutations_do_not_probe_yolo() -> None:
    """`--confirm` remains sufficient on a v7 image that lacks orchestration routes."""
    with (
        patch("thytrader.agent_orchestration.confirmation.fetch_orchestration_status") as fetch,
        patch("thytrader.agent_orchestration.confirmation.record_skipped_confirmation") as skip,
    ):
        require_mutation_confirmation(
            confirmed=True,
            missing_message="Pass --confirm",
            error_type=RuntimeError,
            base_url="http://127.0.0.1:8200",
            tier=YoloTier.DATA,
            command="watch-add",
        )
    fetch.assert_not_called()
    skip.assert_not_called()


def test_hard_gate_does_not_probe_yolo() -> None:
    """Risk-policy writes and live place-order never consult the YOLO advertisement."""
    with (
        patch("thytrader.agent_orchestration.confirmation.fetch_orchestration_status") as fetch,
        pytest.raises(RuntimeError, match="Pass --confirm"),
    ):
        require_mutation_confirmation(
            confirmed=False,
            missing_message="Pass --confirm",
            error_type=RuntimeError,
            base_url="http://127.0.0.1:8200",
            tier=YoloTier.PAPER,
            command="start",
            hard_gate=True,
        )
    fetch.assert_not_called()


def test_yolo_skip_records_then_returns() -> None:
    """An advertised tier records a skip audit instead of requiring `--confirm`."""
    status = AgentOrchestrationStatus(
        confirmation_mode=ConfirmationMode.YOLO,
        yolo_enabled=True,
        yolo_tiers=(YoloTier.RESEARCH,),
    )
    with (
        patch(
            "thytrader.agent_orchestration.confirmation.require_matching_ops_contract"
        ) as preflight,
        patch(
            "thytrader.agent_orchestration.confirmation.fetch_orchestration_status",
            return_value=status,
        ),
        patch("thytrader.agent_orchestration.confirmation.record_skipped_confirmation") as skip,
    ):
        require_mutation_confirmation(
            confirmed=False,
            missing_message="Pass --confirm",
            error_type=RuntimeError,
            base_url="http://127.0.0.1:8200",
            tier=YoloTier.RESEARCH,
            command="create-draft",
        )
    preflight.assert_called_once_with("http://127.0.0.1:8200")
    skip.assert_called_once_with(
        "http://127.0.0.1:8200",
        tier=YoloTier.RESEARCH,
        command="create-draft",
    )


def test_paper_runtime_yolo_rejects_live_mode() -> None:
    """Paper YOLO still requires `--confirm` when the target deployment is live."""
    status = AgentOrchestrationStatus(
        confirmation_mode=ConfirmationMode.YOLO,
        yolo_enabled=True,
        yolo_tiers=(YoloTier.PAPER,),
    )
    with (
        patch("thytrader.agent_orchestration.confirmation.require_matching_ops_contract"),
        patch(
            "thytrader.agent_orchestration.confirmation.fetch_orchestration_status",
            return_value=status,
        ),
        patch("thytrader.agent_orchestration.confirmation.record_skipped_confirmation") as skip,
        pytest.raises(RuntimeError, match="Pass --confirm"),
    ):
        require_paper_runtime_confirmation(
            confirmed=False,
            missing_message="Pass --confirm",
            error_type=RuntimeError,
            base_url="http://127.0.0.1:8200",
            command="pause",
            deployment_mode=lambda: "live",
        )
    skip.assert_not_called()


def test_live_runtime_yolo_skips_live_mode() -> None:
    """Live YOLO records a skip for live pause/resume/stop without covering paper."""
    status = AgentOrchestrationStatus(
        confirmation_mode=ConfirmationMode.YOLO,
        yolo_enabled=True,
        yolo_tiers=(YoloTier.LIVE,),
    )
    with (
        patch("thytrader.agent_orchestration.confirmation.require_matching_ops_contract"),
        patch(
            "thytrader.agent_orchestration.confirmation.fetch_orchestration_status",
            return_value=status,
        ),
        patch("thytrader.agent_orchestration.confirmation.record_skipped_confirmation") as skip,
    ):
        require_paper_runtime_confirmation(
            confirmed=False,
            missing_message="Pass --confirm",
            error_type=RuntimeError,
            base_url="http://127.0.0.1:8200",
            command="pause",
            deployment_mode=lambda: "live",
        )
    skip.assert_called_once_with(
        "http://127.0.0.1:8200",
        tier=YoloTier.LIVE,
        command="pause",
    )


def test_live_runtime_yolo_rejects_paper_mode() -> None:
    """Live YOLO still requires `--confirm` when the target deployment is paper."""
    status = AgentOrchestrationStatus(
        confirmation_mode=ConfirmationMode.YOLO,
        yolo_enabled=True,
        yolo_tiers=(YoloTier.LIVE,),
    )
    with (
        patch("thytrader.agent_orchestration.confirmation.require_matching_ops_contract"),
        patch(
            "thytrader.agent_orchestration.confirmation.fetch_orchestration_status",
            return_value=status,
        ),
        patch("thytrader.agent_orchestration.confirmation.record_skipped_confirmation") as skip,
        pytest.raises(RuntimeError, match="Pass --confirm"),
    ):
        require_paper_runtime_confirmation(
            confirmed=False,
            missing_message="Pass --confirm",
            error_type=RuntimeError,
            base_url="http://127.0.0.1:8200",
            command="pause",
            deployment_mode=lambda: "paper",
        )
    skip.assert_not_called()
