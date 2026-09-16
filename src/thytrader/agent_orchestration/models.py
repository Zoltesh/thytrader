"""Typed contracts for agent orchestration status and YOLO confirmation tiers."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID  # noqa: TC003 - Pydantic resolves this annotation at runtime.

from pydantic import BaseModel, ConfigDict, Field

ORCHESTRATION_SCHEMA_VERSION: Literal["thytrader-agent-orchestration-v1"] = (
    "thytrader-agent-orchestration-v1"
)
PLAYBOOK_RUN_SCHEMA_VERSION: Literal["thytrader-playbook-run-v1"] = "thytrader-playbook-run-v1"
PLAYBOOK_SEQUENCE: tuple[str, ...] = (
    "data_healthy",
    "draft_publish",
    "backtest",
    "optional_paper",
)


class YoloTier(StrEnum):
    """Surfaces that may skip `--confirm` when YOLO is explicitly enabled.

    Live YOLO never skips ``--i-understand-live``. Risk-policy publication,
    live place-order, ``--local`` research, and memory stay hard-gated.
    Paper YOLO never covers a live deployment.
    """

    DATA = "data"
    RESEARCH = "research"
    PAPER = "paper"
    LIVE = "live"


class ConfirmationMode(StrEnum):
    """Safe mode requires `--confirm`; YOLO skips it only on advertised tiers."""

    SAFE = "safe"
    YOLO = "yolo"


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentOrchestrationStatus(_FrozenModel):
    """Read-only confirmation-mode advertisement for agent CLIs and the playbook.

    ``live_hard_gate`` means ``--i-understand-live`` is never skipped and the
    playbook never receives live authority. It does not forbid a ``live`` YOLO
    tier from skipping ``--confirm`` on live start/pause/resume/stop.
    """

    schema_version: Literal["thytrader-agent-orchestration-v1"] = ORCHESTRATION_SCHEMA_VERSION
    confirmation_mode: ConfirmationMode
    yolo_enabled: bool
    yolo_tiers: tuple[YoloTier, ...]
    live_hard_gate: Literal[True] = True
    live_authority: Literal[False] = False
    playbook_sequence: tuple[str, ...] = PLAYBOOK_SEQUENCE

    def allows(self, tier: YoloTier) -> bool:
        """True when YOLO is on and this tier may skip `--confirm`."""
        return self.yolo_enabled and tier in self.yolo_tiers


class SkippedConfirmationRequest(_FrozenModel):
    """Ask the API to audit one YOLO-skipped confirmation before a mutation."""

    tier: YoloTier
    command: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")


class SkippedConfirmationResponse(_FrozenModel):
    """Identity of the durable skipped-confirmation audit event."""

    id: UUID
    category: str
    action: Literal["confirm_skipped"] = "confirm_skipped"
    outcome: Literal["info"] = "info"
    tier: YoloTier
    command: str


class PlaybookIdentities(_FrozenModel):
    """Optional identities collected from child CLIs during one playbook run."""

    strategy_id: str | None = None
    strategy_fingerprint: str | None = None
    run_fingerprint: str | None = None
    result_fingerprint: str | None = None
    deployment_id: str | None = None
    deployment_mode: Literal["paper"] | None = None


class PlaybookStep(_FrozenModel):
    """One child CLI invocation recorded by a playbook run."""

    name: str
    cli: str
    argv: tuple[str, ...]
    ok: Literal[True] = True
    payload: dict[str, object] = Field(
        description="Lane-specific JSON object from the child CLI stdout."
    )


class PlaybookRun(_FrozenModel):
    """Playbook run receipt. Live start is never represented."""

    schema_version: Literal["thytrader-playbook-run-v1"] = PLAYBOOK_RUN_SCHEMA_VERSION
    confirmation_mode: ConfirmationMode
    yolo_enabled: bool
    yolo_tiers: tuple[YoloTier, ...]
    live_hard_gate: Literal[True] = True
    live_started: Literal[False] = False
    live_authority: Literal[False] = False
    steps: tuple[PlaybookStep, ...]
    identities: PlaybookIdentities
