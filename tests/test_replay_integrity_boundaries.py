"""Repaired financial facts are denied symmetrically at every artifact trust boundary."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path

import pytest

from tests.test_replay_cli import cli
from thytrader.replay.codec import wire_value
from thytrader.replay.models import canonical_bytes, fingerprint
from thytrader.replay.packages import Manifest, load, publish, read_input


@pytest.mark.parametrize("fault", ["precision", "currency", "span", "marks"])
def test_input_publication_and_resigned_read_reject_same_forgery(
    tmp_path: Path, fault: str
) -> None:
    """A self-consistent digest or model-copy type cannot authorize contradictory facts."""
    package = read_input(Path("tests/replay/fixtures/usdc-lifecycle.input.json"))
    if fault == "precision":
        assert package.ledger is not None
        fill = package.ledger.fills[0].model_copy(update={"quantity": "1" + "0" * 63 + "1"})
        package = package.model_copy(
            update={"ledger": package.ledger.model_copy(update={"fills": (fill,)})}
        )
    else:
        step = package.steps[0]
        if fault == "currency":
            step = step.model_copy(
                update={"product": replace(step.product, base_currency="BTC", quote_currency="USD")}
            )
        elif fault == "span":
            event = step.broker_events[0].model_copy(
                update={
                    "started_at": datetime(2099, 1, 1, tzinfo=UTC),
                    "completed_at": datetime(1900, 1, 1, tzinfo=UTC),
                }
            )
            step = step.model_copy(update={"broker_events": (event, *step.broker_events[1:])})
        else:
            step = step.model_copy(
                update={"marks": (("ETH-USDC", Decimal(1)), ("ETH-USDC", Decimal(999)))}
            )
        package = package.model_copy(update={"steps": (step,)})
    raw = json.dumps(
        wire_value(package.model_dump(mode="python")), sort_keys=True, separators=(",", ":")
    ).encode()
    source = tmp_path / "input.json"
    source.write_bytes(raw)
    with pytest.raises(ValueError):
        canonical_bytes(read_input(source))
    root = tmp_path / "published"
    with pytest.raises(ValueError):
        publish(root, package)
    assert not root.exists()
    # This intentionally bypasses the writer to test an untrusted read with a valid digest.
    directory = tmp_path / fingerprint(raw).removeprefix("sha256:")
    directory.mkdir()
    (directory / "evidence.json").write_bytes(raw)
    manifest = Manifest(
        schema_version="thytrader-replay-manifest-v1",
        fingerprint=fingerprint(raw),
        payload="evidence.json",
        size=len(raw),
    )
    path = directory / "manifest.json"
    path.write_text(manifest.model_dump_json())
    with pytest.raises(ValueError):
        load(path)


@pytest.mark.parametrize(
    "fault", ["million-tiny", "million-large", "over64", "derived-overflow", "derived-underflow"]
)
def test_cli_financial_refusal_is_redacted_exit_two(tmp_path: Path, fault: str) -> None:
    """Original numeric exploits cannot falsely match or escape as a raw CLI traceback."""
    package = read_input(Path("tests/replay/fixtures/partial-loss.input.json"))
    assert package.ledger is not None
    quantity, price = "1", "1"
    if fault == "million-tiny":
        quantity = "0." + "0" * 1000100 + "1"
    elif fault == "million-large":
        price = quantity = "1" + "0" * 500000
    elif fault == "over64":
        quantity = "1" + "0" * 63 + "1"
    elif fault == "derived-overflow":
        price, quantity = "1" + "0" * 128, "10"
    else:
        price, quantity = "0." + "0" * 127 + "1", "0.1"
    fill = package.ledger.fills[0].model_copy(
        update={"price": price, "quantity": quantity, "fee": "0"}
    )
    ledger = package.ledger.model_copy(
        update={"starting_cash": "0", "observed_ending_cash": "0", "fills": (fill,)}
    )
    package = package.model_copy(update={"ledger": ledger})
    source = tmp_path / "input.json"
    source.write_text(package.model_dump_json())
    exported = cli("export", "--input", str(source), "--root", str(tmp_path / "packages"))
    result = exported
    if fault.startswith("derived"):
        assert exported.returncode == 0, exported.stderr
        result = cli("run", json.loads(exported.stdout)["manifest"])
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "thytrader-replay: evidence rejected or unavailable\n"
