"""Canonical SHA-256 identities and JSON of research study requests, plans, and studies."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from thytrader.research.study_models import (
        ResearchStudy,
        ResearchStudyPlan,
        ResearchStudyRequest,
    )

_FINGERPRINT_PREFIX = "sha256:"


def request_fingerprint(request: ResearchStudyRequest) -> str:
    """Return the SHA-256 identity of the canonical study request."""
    payload = request.model_dump(mode="json", exclude_none=True)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{_FINGERPRINT_PREFIX}{sha256(canonical.encode()).hexdigest()}"


def plan_fingerprint(plan: ResearchStudyPlan) -> str:
    """Return the SHA-256 identity of the effective child window schedule."""
    windows = [window.model_dump(mode="json", exclude_none=True) for window in plan.windows]
    payload = {
        "kind": plan.kind.value,
        "timeframe": plan.timeframe,
        "windows": windows,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{_FINGERPRINT_PREFIX}{sha256(canonical.encode()).hexdigest()}"


def study_fingerprint(study: ResearchStudy) -> str:
    """Return the SHA-256 identity of the derived study excluding its own fingerprint."""
    payload = study.model_dump(mode="json", exclude_none=True)
    payload.pop("study_fingerprint", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{_FINGERPRINT_PREFIX}{sha256(canonical.encode()).hexdigest()}"


def canonical_study_json(study: ResearchStudy) -> str:
    """Return the stored canonical study document including its fingerprint."""
    payload = study.model_dump(mode="json", exclude_none=True)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
