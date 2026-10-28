"""What an answer was produced from, and the refusals of an answer from elsewhere (ADR 0147)."""

from __future__ import annotations

from dataclasses import dataclass

from thytrader.agent_desk.models import WakeOutcome

STALE_CONFIG = {
    "outcome": WakeOutcome.REFUSED,
    "outcome_code": "DESK_CONFIG_CHANGED",
    "detail": "The desk's harness, model, mandate or caps changed after this briefing.",
}


LIVE_STRANGER = {
    "outcome": WakeOutcome.REFUSED,
    "outcome_code": "DESK_LIVE_IDENTITY_MISMATCH",
    "detail": "A live desk trades only with its configured harness and model.",
}


@dataclass(frozen=True, slots=True)
class AnswerFingerprints:
    """What an answer was produced from: its briefing's material and configuration, its runner."""

    material: str | None = None
    config: str | None = None
    runner: str | None = None
