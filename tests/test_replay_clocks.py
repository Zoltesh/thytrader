"""As-seen auxiliary clocks drive both real signal evaluators without lookahead."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path

from tests.test_replay_cli import cli
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint


def test_reference_window_replays_on_its_completed_clock(tmp_path: Path) -> None:
    """A reference input is reproduced from exact captured bars, never refetched today."""
    payload = json.loads(Path("tests/replay/fixtures/no-signal.input.json").read_text())
    definition = payload["strategy"]
    definition["data_requirements"]["reference_instruments"] = [
        {"id": "reference", "product_id": "ETH-USD", "timeframe": "2h"}
    ]
    definition["indicators"].append(
        {
            "id": "reference_close",
            "kind": "identity",
            "input": "close",
            "source": "reference",
            "parameters": {},
        }
    )
    definition["entry"]["when"]["all"].append(
        {
            "left": {"indicator": "reference_close"},
            "operator": "greater_than",
            "right": {"literal": "1"},
        }
    )
    checked = StrategyDefinition.model_validate_json(json.dumps(definition))
    step = payload["steps"][0]
    step["before"]["deployment"]["strategy_fingerprint"] = strategy_fingerprint(checked)
    latest = datetime.fromisoformat(step["candles"][-1]["starts_at"])
    close = latest + timedelta(hours=1)
    anchor = close.replace(hour=(close.hour // 2) * 2, minute=0) - timedelta(hours=2)
    step["reference_windows"] = [
        {
            "key": "reference",
            "product_id": "ETH-USD",
            "timeframe": "2h",
            "candles": [
                {
                    "starts_at": (anchor - timedelta(hours=2 * i))
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "open": "42",
                    "high": "43",
                    "low": "41",
                    "close": "42",
                    "volume": "1",
                }
                for i in reversed(range(5))
            ],
        }
    ]
    source = tmp_path / "reference.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    exported = cli("export", "--input", str(source), "--root", str(tmp_path / "packages"))
    assert exported.returncode == 0, exported.stderr
    result = cli("run", json.loads(exported.stdout)["manifest"])
    assert result.returncode == 3, result.stderr
    report = json.loads(result.stdout)
    assert report["execution"]["status"] == "match"
    assert report["same_config_research_execution"]["status"] == "match"
    assert {v["indicator_id"]: v["value"] for v in report["signals"][0]["indicator_values"]}[
        "reference_close"
    ] == "42"
