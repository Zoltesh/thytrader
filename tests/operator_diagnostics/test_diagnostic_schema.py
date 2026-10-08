"""Shipped schema and skill text cover safe account-read and audit evidence."""

import json
from pathlib import Path

from thytrader.exchanges.read_errors import ExchangeReadFailure
from thytrader.operator.runtime_models import AuditFailureEvidence

_ROOT = Path(__file__).resolve().parents[2]


def test_committed_schema_matches_diagnostic_evidence_models() -> None:
    """Field or enum changes must also update the schema operator agents read."""
    schema = json.loads(
        (_ROOT / "skills/thytrader-operator/references/operator-report-v1.schema.json").read_text()
    )
    failure = ExchangeReadFailure.model_json_schema()
    nested = failure.pop("$defs")
    assert schema["$defs"]["ExchangeReadFailure"] == failure
    for name, definition in nested.items():
        assert schema["$defs"][name] == definition
    assert schema["$defs"]["AuditFailureEvidence"] == AuditFailureEvidence.model_json_schema()
    skill = (_ROOT / "skills/thytrader-operator/SKILL.md").read_text()
    for field in (*failure["properties"], *schema["$defs"]["AuditFailureEvidence"]["properties"]):
        assert field in skill
