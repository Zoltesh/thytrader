"""A live start records its evidence and warns when it is thin; refusing is opt-in (ADR 0143)."""

from __future__ import annotations

import asyncio
from typing import Any

from tests.api.test_deployments import _client, _published_strategy
from tests.strategy_fakes import SeededStrategyStore as InMemoryPublicationStore
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.risk.start_safeguards import LiveEvidenceThresholds, StartSafeguards
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.strategies.models import strategy_fingerprint
from thytrader.strategies.snapshots import StrategySnapshot
from thytrader.trading.memory import InMemoryExecutionStore


def _policy(safeguards: StartSafeguards | None) -> InMemoryRiskPolicyStore:
    """A published policy with ``safeguards``."""
    store = InMemoryRiskPolicyStore()
    policy = compiled_default_risk_policy().model_copy(update={"start_safeguards": safeguards})
    asyncio.run(store.publish(policy))
    return store


def _start(
    mode: str, safeguards: StartSafeguards | None = None
) -> tuple[Any, Any, InMemoryExecutionStore]:
    """Start the template strategy in ``mode``; return the response and the read-back."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )
    with _client(publication, execution, live_credentials=True, risk=_policy(safeguards)) as client:
        body: dict[str, object] = {"strategy_id": str(definition.strategy_id), "mode": mode}
        if mode == "live":
            body["i_understand_live"] = True
        else:
            body.update(
                {
                    "paper_starting_cash": "1000",
                    "maker_fee_rate": "0.004",
                    "taker_fee_rate": "0.006",
                }
            )
        started = client.post("/api/v1/deployments", json=body)
        read = (
            client.get(f"/api/v1/deployments/{started.json()['id']}/live-evidence")
            if started.status_code == 201
            else None
        )
    return started, read, execution


def test_a_thin_live_start_warns_and_records_its_evidence() -> None:
    """No study and no paper bot: the bot starts, warns, and keeps the evidence."""
    started, read, _execution = _start("live")
    assert started.status_code == 201, started.text
    payload = started.json()
    (warning,) = [item for item in payload["warnings"] if item.startswith("LIVE_EVIDENCE")]
    assert warning.startswith("LIVE_EVIDENCE_THIN: ")
    evidence = payload["live_evidence"]
    assert evidence["schema_version"] == "thytrader-live-start-evidence-v1"
    assert evidence["out_of_sample_windows"] == 0
    assert evidence["paper_deployments"] == 0
    assert evidence["thin_reasons"] == [
        "no out-of-sample study window for this exact strategy fingerprint",
        "no paper book of this exact strategy fingerprint",
    ]
    assert read is not None
    assert read.status_code == 200
    assert read.json() == evidence


def test_the_opt_in_safeguard_refuses_a_thin_live_start() -> None:
    """Under require_live_evidence nothing is created and the refusal names the gaps."""
    started, read, execution = _start("live", StartSafeguards(require_live_evidence=True))
    assert started.status_code == 409
    detail = started.json()["detail"]
    assert detail.startswith("LIVE_EVIDENCE_REQUIRED: ")
    assert "Nothing was started" in detail
    assert read is None
    assert asyncio.run(execution.list_deployments()) == ()


def test_relaxed_thresholds_start_without_a_warning() -> None:
    """An operator who requires no research or paper days gets no thin warning."""
    relaxed = StartSafeguards(
        live_evidence=LiveEvidenceThresholds(
            min_out_of_sample_trades=0, require_positive_out_of_sample=False, min_paper_days=0
        )
    )
    started, read, _execution = _start("live", relaxed)
    assert started.status_code == 201
    payload = started.json()
    assert not [item for item in payload["warnings"] if item.startswith("LIVE_EVIDENCE")]
    assert payload["live_evidence"]["thin_reasons"] == []
    assert read is not None
    assert read.status_code == 200


def test_a_paper_start_records_no_live_evidence() -> None:
    """Paper bots carry no live-start evidence; reading it is a 404."""
    started, read, _execution = _start("paper")
    assert started.status_code == 201, started.text
    assert started.json()["live_evidence"] is None
    assert read is not None
    assert read.status_code == 404
