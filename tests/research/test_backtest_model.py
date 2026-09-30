"""Tests for the single unified backtest model description (ADR 0083)."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.research.backtest_model import backtest_model_description


def test_backtest_model_names_one_unversioned_engine_and_its_assumptions() -> None:
    """The description carries one engine id and every disclosed fill assumption."""
    description = backtest_model_description()

    assert description.engine == "thytrader-backtest"
    assert description.decision_record == "ADR 0083"
    keys = [assumption.key for assumption in description.assumptions]
    assert keys == list(dict.fromkeys(keys))
    for required in (
        "maker_entries",
        "unfilled_entries",
        "stops_and_targets",
        "fees",
        "slippage",
        "spread_stress",
        "queue_position",
    ):
        assert required in keys
    assert "not a promise" in description.honesty
    text = " ".join(f"{item.label} {item.detail}" for item in description.assumptions)
    assert re.search(r"\bV[1-4]\b|thytrader-bar-", text) is None


def test_backtest_model_route_replaces_the_engine_support_matrix() -> None:
    """Agents read the model description over HTTP; the old matrix route is gone."""
    app = create_app(Settings(_env_file=None))

    with TestClient(app) as client:
        model = client.get("/api/v1/research/backtest-model")
        removed = client.get("/api/v1/research/engine-support")

    assert model.status_code == 200
    assert model.json()["engine"] == "thytrader-backtest"
    assert removed.status_code == 404
