"""The shipped operator schema exposes the exact derived fee-evidence contract."""

import json
from pathlib import Path

from thytrader.backtest.cost_attribution import BacktestCostAttribution


def test_operator_schema_matches_cost_attribution_contract() -> None:
    """Amount or identity changes must ship in the schema operator agents consume."""
    root = Path(__file__).resolve().parents[2]
    schema = json.loads(
        (root / "skills/thytrader-operator/references/operator-report-v1.schema.json").read_text()
    )
    assert schema["$defs"]["BacktestCostAttribution"] == BacktestCostAttribution.model_json_schema()
    assert schema["$defs"]["PerformancePayload"]["properties"]["cost_attribution"]["anyOf"] == [
        {"$ref": "#/$defs/BacktestCostAttribution"},
        {"type": "null"},
    ]
