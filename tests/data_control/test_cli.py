"""CLI tests for confirmation-gated market-data mutations."""

from __future__ import annotations

import pytest

from thytrader.data_control.cli import main


def test_data_help_describes_confirm_and_boundaries(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operators can discover the data CLI without an API."""
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "--confirm" in output
    assert "watch-add" in output
    assert "inspect-gaps" in output
    assert "not the operator" in output.lower() or "does not place orders" in output.lower()


def test_watch_add_without_confirm_does_not_call_api() -> None:
    """Omitting --confirm must exit before HTTP."""
    with pytest.raises(SystemExit) as raised:
        main(["watch-add", "--product-id", "ETH-USD", "--timeframe", "5m"])
    assert raised.value.code != 0
    assert "Pass --confirm" in str(raised.value)


def test_ingest_without_confirm_does_not_call_api() -> None:
    """Ingest is a mutation and requires --confirm."""
    with pytest.raises(SystemExit) as raised:
        main(["ingest", "--product-id", "ETH-USD", "--timeframe", "5m"])
    assert raised.value.code != 0
    assert "Pass --confirm" in str(raised.value)
