"""HTTP contracts for paper and live strategy deployments."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr

from tests.strategy_fakes import SeededStrategyStore as InMemoryPublicationStore
from thytrader.api.app import create_app
from thytrader.audit_events import AuditEventCategory, InMemoryAuditEventStore
from thytrader.config import Environment, Settings
from thytrader.execution.capital import apply_venue_quote
from thytrader.execution.ids import utc_now, uuid7
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentStatus,
    Fill,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    PositionSide,
    RuntimePhase,
    with_runtime,
)
from thytrader.risk.breakers import EntryObservation
from thytrader.risk.gate import ProposedEntry, evaluate_new_entry
from thytrader.risk.models import RiskDecision, RiskReasonCode, compiled_default_risk_policy
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.security.models import INSTALLATION_AUTH_HEADER
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.models import (
    Instrument,
    StrategyDefinition,
    strategy_fingerprint,
)
from thytrader.strategies.snapshots import (
    StrategySnapshot,
)


def _published_strategy() -> StrategyDefinition:
    """Return the reference strategy marked published."""
    draft = create_template_strategy(now=datetime(2026, 1, 1, tzinfo=UTC))
    return StrategyDefinition.model_validate({**draft.model_dump(mode="python")})


def _client(
    publication: InMemoryPublicationStore,
    execution: InMemoryExecutionStore,
    *,
    live_credentials: bool = False,
    audit: InMemoryAuditEventStore | None = None,
    risk: InMemoryRiskPolicyStore | None = None,
) -> TestClient:
    """Build an API client with in-memory publication and execution stores."""
    settings = Settings(_env_file=None)
    if live_credentials:
        settings = Settings(
            _env_file=None,
            coinbase_api_key_name=SecretStr("key"),
            coinbase_api_private_key=SecretStr("secret"),
        )
    app = create_app(
        settings,
        strategy_store=publication,
        execution_store=execution,
        audit_event_store=audit,
        risk_policy_store=risk,
    )
    return TestClient(app)


def _published_risk_policy_store() -> InMemoryRiskPolicyStore:
    """Return a risk-policy store with a published (non-compiled-default) policy.

    Live deployments require an operator-published policy (audit F25); tests that
    exercise a live start with valid credentials publish one here.
    """
    store = InMemoryRiskPolicyStore()
    asyncio.run(store.publish(compiled_default_risk_policy()))
    return store


def test_live_detail_reports_pinned_performance_capital_without_funding_ledger_cash() -> None:
    """The HTTP response uses the new percentage basis while preserving dollar PnL."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )
    deployment = Deployment(
        id=uuid7(utc_now()),
        strategy_id=definition.strategy_id,
        strategy_fingerprint=fingerprint,
        product_id="BTC-USD",
        timeframe="1h",
        mode=DeploymentMode.LIVE,
        status=DeploymentStatus.RUNNING,
        phase=RuntimePhase.FLAT,
        cash=Decimal("-0.5"),
        initial_equity=Decimal("0"),
        high_water_mark_equity=Decimal("0"),
        performance_capital_quote=Decimal("100"),
        performance_maximum_drawdown_fraction=Decimal("0.005"),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    asyncio.run(execution.create_deployment(deployment))
    with _client(publication, execution) as client:
        response = client.get(f"/api/v1/deployments/{deployment.id}?detail=full")
    assert response.status_code == 200
    payload = response.json()
    assert payload["cash"] == "-0.5"
    assert payload["capital"]["performance_capital_quote"] == "100"
    assert payload["capital"]["initial_equity"] == "0"
    assert payload["capital"]["performance_maximum_drawdown_fraction"] == "0.005"
    assert payload["ledger"]["total_net_pnl"] == "-0.5"
    assert payload["ledger"]["total_return_fraction"] == "-0.005"


def test_live_start_initializes_sibling_daily_loss_baselines() -> None:
    """Fresh flat peers have exact zero ledger baselines before any worker cycle runs."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definitions = (_published_strategy(), _published_strategy())
    for definition in definitions:
        fingerprint = strategy_fingerprint(definition)
        publication.published[fingerprint] = StrategySnapshot(
            strategy_fingerprint=fingerprint, definition=definition
        )
    snapshots = []
    with _client(
        publication, execution, live_credentials=True, risk=_published_risk_policy_store()
    ) as client:
        for definition in definitions:
            response = client.post(
                "/api/v1/deployments",
                json={
                    "strategy_id": str(definition.strategy_id),
                    "mode": "live",
                    "i_understand_live": True,
                },
            )
            assert response.status_code == 201
            snapshot = asyncio.run(execution.get_deployment(UUID(response.json()["id"])))
            assert snapshot.deployment.initial_equity == Decimal("0")
            assert snapshot.deployment.utc_day_open_equity == Decimal("0")
            assert snapshot.deployment.utc_day_open_at is not None
            # The worker observes and pins an unallocated book's venue budget before entry.
            observed = apply_venue_quote(
                snapshot.deployment, available=Decimal("100"), now=utc_now()
            )
            snapshots.append(replace(snapshot, deployment=observed))
    verdict = evaluate_new_entry(
        compiled_default_risk_policy(),
        mode=DeploymentMode.LIVE,
        proposed=ProposedEntry("BTC-USD", definitions[0].strategy_id, Decimal("7")),
        snapshots=snapshots,
        live_quote_cash=Decimal("100"),
        observation=EntryObservation(
            as_of=utc_now(),
            proposed_price=Decimal("100"),
            reference_price=Decimal("100"),
            marks={"BTC-USD": Decimal("100")},
        ),
    )
    assert verdict.decision is RiskDecision.ALLOW


def test_paper_deployment_persists_and_updates_library_status() -> None:
    """POST paper starts a running deployment and the library reports that status."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)

    with _client(publication, execution) as client:
        publication.published[fingerprint] = StrategySnapshot(
            strategy_fingerprint=fingerprint, definition=definition
        )
        created = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "paper",
                "paper_starting_cash": "10000",
            },
        )
        listed = client.get("/api/v1/deployments")
        fetched = client.get(f"/api/v1/deployments/{created.json()['id']}")
        library = client.get("/api/v1/strategies")

    assert created.status_code == 201
    body = created.json()
    assert body["mode"] == "paper"
    assert body["status"] == "running"
    assert body["phase"] == "flat"
    assert body["cash"] == "10000"
    assert body["maker_fee_rate"] == "0.001"
    assert body["taker_fee_rate"] == "0.002"
    assert listed.status_code == 200
    assert listed.json()["deployments"][0]["id"] == body["id"]
    assert fetched.status_code == 200
    assert fetched.json()["orders"] == []
    entry = next(
        item
        for item in library.json()["strategies"]
        if item["strategy_id"] == str(definition.strategy_id)
    )
    assert entry["paper_live"] == {"paper": "running", "live": "none"}


def test_paper_deployment_mutation_requires_installation_auth_when_boundary_enabled() -> None:
    """Unauthenticated POST is rejected when ADR 0061 trust boundary is on."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )
    settings = Settings(
        environment=Environment.TEST,
        installation_token=SecretStr("0060-boundary-token"),
        trust_boundary_enabled=True,
        _env_file=None,
    )
    app = create_app(
        settings,
        strategy_store=publication,
        execution_store=execution,
    )
    payload = {
        "strategy_id": str(definition.strategy_id),
        "mode": "paper",
        "paper_starting_cash": "10000",
    }
    with TestClient(app) as client:
        denied = client.post("/api/v1/deployments", json=payload)
        allowed = client.post(
            "/api/v1/deployments",
            json=payload,
            headers={INSTALLATION_AUTH_HEADER: "Bearer 0060-boundary-token"},
        )

    assert denied.status_code == 401
    assert allowed.status_code == 201
    body = allowed.json()
    assert [item["product_id"] for item in body["instrument_runtimes"]] == ["BTC-USD"]
    assert body["book_totals"] == {"open_books": 0, "working_orders": 0, "fill_count": 0}


def test_pause_resume_and_stop_deployment() -> None:
    """Pause, resume, and stop follow the documented lifecycle."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution) as client:
        created = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "paper",
                "paper_starting_cash": "5000",
            },
        )
        deployment_id = created.json()["id"]
        paused = client.post(f"/api/v1/deployments/{deployment_id}/pause")
        resumed = client.post(f"/api/v1/deployments/{deployment_id}/resume")
        stopped = client.post(f"/api/v1/deployments/{deployment_id}/stop")
        rejected = client.post(f"/api/v1/deployments/{deployment_id}/resume")

    assert paused.json()["status"] == "paused"
    assert resumed.json()["status"] == "running"
    assert stopped.json()["status"] == "stopped"
    assert rejected.status_code == 409


def test_deployment_response_includes_lifecycle_and_capital_fields() -> None:
    """Runtime show exposes ADR 0058 lifecycle and capital fields over HTTP."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution) as client:
        created = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "paper",
                "paper_starting_cash": "5000",
            },
        )

    body = created.json()
    assert body["lifecycle_command"] == "none"
    assert body["daily_loss_latched"] is False
    assert body["drawdown_latched"] is False
    assert body["revision"] == 0
    assert body["worker_lease_held"] is False
    capital = body["capital"]
    assert capital["allocated_capital"] is None
    assert capital["venue_available_quote"] is None


def test_deployment_list_includes_lifecycle_observability_fields() -> None:
    """List deployments returns the same runtime observability fields as show."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution) as client:
        client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "paper",
                "paper_starting_cash": "5000",
            },
        )
        listed = client.get("/api/v1/deployments")

    body = listed.json()["deployments"][0]
    assert body["lifecycle_command"] == "none"
    assert body["daily_loss_latched"] is False
    assert body["drawdown_latched"] is False
    assert body["revision"] == 0
    assert body["worker_lease_held"] is False
    assert "capital" in body


def test_deployment_list_resolves_timeframe_from_published_strategy() -> None:
    """Legacy rows with null deployment.timeframe copy the published strategy clock."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy().model_copy(update={"timeframe": "2h"})
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )
    now = datetime(2026, 9, 17, tzinfo=UTC)
    asyncio.run(
        execution.create_deployment(
            Deployment(
                id=uuid4(),
                strategy_fingerprint=fingerprint,
                strategy_id=definition.strategy_id,
                product_id=definition.instrument.product_id,
                mode=DeploymentMode.PAPER,
                status=DeploymentStatus.RUNNING,
                cash=Decimal("100"),
                phase=RuntimePhase.FLAT,
                created_at=now,
                updated_at=now,
                paper_starting_cash=Decimal("100"),
            )
        )
    )

    with _client(publication, execution) as client:
        listed = client.get("/api/v1/deployments")

    assert listed.json()["deployments"][0]["timeframe"] == "2h"


def test_create_deployment_persists_strategy_timeframe() -> None:
    """New deployments store the published strategy clock for runtime list/show."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy().model_copy(update={"timeframe": "2h"})
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution) as client:
        created = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "paper",
                "paper_starting_cash": "5000",
            },
        )

    assert created.json()["timeframe"] == "2h"


def test_reset_breaker_latches_clears_latched_breakers() -> None:
    """Explicit operator reset clears latched breakers and breaker mismatch detail."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution) as client:
        created = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "paper",
                "paper_starting_cash": "5000",
            },
        )
        deployment_id = UUID(created.json()["id"])
        snapshot = asyncio.run(execution.get_deployment(deployment_id))
        latched = with_runtime(
            snapshot.deployment,
            updated_at=utc_now(),
            daily_loss_latched=True,
            mismatch_detail=(
                f"{RiskReasonCode.DAILY_LOSS_LIMIT.value}: Daily-loss breaker is latched."
            ),
        )
        asyncio.run(execution.save_deployment(latched))
        reset = client.post(f"/api/v1/deployments/{deployment_id}/reset-breaker-latches")
        empty = client.post(f"/api/v1/deployments/{deployment_id}/reset-breaker-latches")

    body = reset.json()
    assert body["daily_loss_latched"] is False
    assert body["drawdown_latched"] is False
    assert body["mismatch_detail"] is None
    assert empty.status_code == 409


def test_duplicate_running_paper_deployment_conflicts() -> None:
    """Only one running paper deployment is allowed per strategy."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )
    payload = {
        "strategy_id": str(definition.strategy_id),
        "mode": "paper",
        "paper_starting_cash": "10000",
    }

    with _client(publication, execution) as client:
        first = client.post("/api/v1/deployments", json=payload)
        second = client.post("/api/v1/deployments", json=payload)

    assert first.status_code == 201
    assert second.status_code == 409


def test_live_deployment_requires_credentials() -> None:
    """Live mode is rejected until Coinbase credentials are configured."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution) as client:
        denied = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "live",
                "i_understand_live": True,
            },
        )

    with _client(
        publication, execution, live_credentials=True, risk=_published_risk_policy_store()
    ) as client:
        allowed = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "live",
                "i_understand_live": True,
            },
        )

    assert denied.status_code == 409
    assert allowed.status_code == 201
    assert allowed.json()["mode"] == "live"
    assert allowed.json()["cash"] == "0"
    assert allowed.json()["maker_fee_rate"] is None
    assert allowed.json()["taker_fee_rate"] is None


def test_five_minute_strategy_can_start_paper_and_live() -> None:
    """Paper and live may evaluate closed 5m bars; live still needs credentials."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy().model_copy(update={"timeframe": "5m"})
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution) as client:
        paper = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "paper",
                "paper_starting_cash": "10000",
            },
        )
        live = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "live",
                "i_understand_live": True,
            },
        )

    with _client(
        publication, execution, live_credentials=True, risk=_published_risk_policy_store()
    ) as client:
        live_with_keys = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "live",
                "i_understand_live": True,
            },
        )

    assert paper.status_code == 201
    assert paper.json()["mode"] == "paper"
    assert live.status_code == 409
    assert live_with_keys.status_code == 201
    assert live_with_keys.json()["mode"] == "live"


def test_one_minute_strategy_can_start_paper_and_live() -> None:
    """Paper and live may evaluate closed 1m bars; live still needs credentials."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy().model_copy(update={"timeframe": "1m"})
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution) as client:
        paper = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "paper",
                "paper_starting_cash": "10000",
            },
        )
        live = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "live",
                "i_understand_live": True,
            },
        )

    with _client(
        publication, execution, live_credentials=True, risk=_published_risk_policy_store()
    ) as client:
        live_with_keys = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "live",
                "i_understand_live": True,
            },
        )

    assert paper.status_code == 201
    assert paper.json()["mode"] == "paper"
    assert live.status_code == 409
    assert live_with_keys.status_code == 201
    assert live_with_keys.json()["mode"] == "live"


def test_fifteen_minute_strategy_can_start_paper() -> None:
    """15m is a legal paper clock, not HTF-only."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy().model_copy(update={"timeframe": "15m"})
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution) as client:
        paper = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "paper",
                "paper_starting_cash": "10000",
            },
        )

    assert paper.status_code == 201
    assert paper.json()["mode"] == "paper"


def test_unknown_fingerprint_is_not_found() -> None:
    """Deploying an unknown strategy fails closed."""
    with _client(InMemoryPublicationStore(), InMemoryExecutionStore()) as client:
        missing = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": "01985cf0-7b60-7000-8000-0000000000ff",
                "mode": "paper",
                "paper_starting_cash": "10000",
            },
        )
        unknown = client.get("/api/v1/deployments/00000000-0000-7000-8000-000000000001")

    assert missing.status_code == 404
    assert unknown.status_code == 404


def test_two_instruments_can_run_paper_together_under_the_default_policy() -> None:
    """BTC and ETH single-instrument publications share the compiled multi-asset envelope."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    btc = _published_strategy()
    eth = create_template_strategy(now=datetime(2026, 1, 2, tzinfo=UTC), product_id="ETH-USD")
    eth = StrategyDefinition.model_validate({**eth.model_dump(mode="python")})
    btc_fp = strategy_fingerprint(btc)
    eth_fp = strategy_fingerprint(eth)
    publication.published[btc_fp] = StrategySnapshot(strategy_fingerprint=btc_fp, definition=btc)
    publication.published[eth_fp] = StrategySnapshot(strategy_fingerprint=eth_fp, definition=eth)

    with _client(publication, execution) as client:
        first = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(btc.strategy_id),
                "mode": "paper",
                "paper_starting_cash": "10000",
            },
        )
        second = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(eth.strategy_id),
                "mode": "paper",
                "paper_starting_cash": "10000",
            },
        )

    assert first.status_code == 201
    assert second.status_code == 201
    assert {first.json()["product_id"], second.json()["product_id"]} == {"BTC-USD", "ETH-USD"}


def test_paper_starting_cash_over_the_book_is_conflict() -> None:
    """Compiled paper_capital_quote 100000 must reject a larger paper start."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution) as client:
        denied = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "paper",
                "paper_starting_cash": "100001",
            },
        )

    assert denied.status_code == 409
    assert "paper_capital_quote" in denied.json()["detail"]


def test_paper_start_records_runtime_audit_without_cash() -> None:
    """Deployment mutations append runtime audit events without cash values."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    audit = InMemoryAuditEventStore()
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution, audit=audit) as client:
        created = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "paper",
                "paper_starting_cash": "10000",
            },
        )

    assert created.status_code == 201
    events = asyncio.run(audit.list_recent())
    assert events
    event = events[0]
    assert event.category is AuditEventCategory.RUNTIME
    assert event.action == "start_paper"
    assert "cash" not in event.detail.lower()
    assert "10000" not in event.detail
    assert fingerprint in event.detail


def test_paper_fee_fields_persist_and_reject_illegal_pairs() -> None:
    """Custom paper rates persist; live and one-sided pairs are conflicts."""
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)

    def _fresh() -> tuple[InMemoryPublicationStore, InMemoryExecutionStore]:
        """Return a published fingerprint bound to an empty execution store."""
        publication = InMemoryPublicationStore()
        publication.published[fingerprint] = StrategySnapshot(
            strategy_fingerprint=fingerprint, definition=definition
        )
        return publication, InMemoryExecutionStore()

    publication, execution = _fresh()
    with _client(publication, execution) as client:
        created = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "paper",
                "paper_starting_cash": "10000",
                "maker_fee_rate": "0.0025",
                "taker_fee_rate": "0.004",
            },
        )
    assert created.status_code == 201
    assert created.json()["maker_fee_rate"] == "0.0025"
    assert created.json()["taker_fee_rate"] == "0.004"

    publication, execution = _fresh()
    with _client(publication, execution) as client:
        one_sided = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "paper",
                "paper_starting_cash": "10000",
                "maker_fee_rate": "0.0025",
            },
        )
        inverted = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "paper",
                "paper_starting_cash": "10000",
                "maker_fee_rate": "0.004",
                "taker_fee_rate": "0.002",
            },
        )
    assert one_sided.status_code == 409
    assert inverted.status_code == 409

    publication, execution = _fresh()
    with _client(publication, execution, live_credentials=True) as live_client:
        live_fees = live_client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "live",
                "i_understand_live": True,
                "maker_fee_rate": "0.001",
                "taker_fee_rate": "0.002",
            },
        )
    assert live_fees.status_code == 409


def _two_product_strategy() -> StrategyDefinition:
    """Return a published BTC primary with ETH extra coverage and two-book cap."""
    definition = _published_strategy()
    extra = Instrument(product_id="ETH-USD", base_currency="ETH", quote_currency="USD")
    limits = definition.portfolio_limits.model_copy(update={"max_concurrent_positions": 2})
    return definition.model_copy(
        update={"additional_instruments": (extra,), "portfolio_limits": limits}
    )


def _at(hour: int) -> datetime:
    """Return a UTC hour on 2026-09-16."""
    return datetime(2026, 9, 16, hour, tzinfo=UTC)


def _open_position(
    *,
    deployment_id: UUID,
    product_id: str,
    quantity: str,
    side: PositionSide,
    entry_price: str,
) -> Position:
    """Return one open product book."""
    now = _at(1)
    return Position(
        deployment_id=deployment_id,
        quantity=Decimal(quantity),
        entry_price=Decimal(entry_price),
        stop_price=Decimal("90") if side is PositionSide.LONG else Decimal("3200"),
        target_price=Decimal("120") if side is PositionSide.LONG else Decimal("2700"),
        entered_bar=now,
        updated_at=now,
        side=side,
        product_id=product_id,
        add_count=1,
    )


def _seed_order(
    *,
    deployment_id: UUID,
    product_id: str,
    side: OrderSide,
    status: OrderStatus,
    quantity: str,
    price: str,
    filled_quantity: str = "0",
) -> Order:
    """Return one venue-visible order tagged with a Coinbase product."""
    now = _at(1)
    return Order(
        id=uuid4(),
        deployment_id=deployment_id,
        intent_id=uuid4(),
        client_order_id=f"{product_id}-{status.value}",
        side=side,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal(quantity),
        status=status,
        created_at=now,
        updated_at=now,
        price=Decimal(price),
        filled_quantity=Decimal(filled_quantity),
        product_id=product_id,
    )


def test_two_product_post_lists_flat_runtimes_without_seeding_store() -> None:
    """A fresh two-product start exposes both overlays as FLAT before any fills."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _two_product_strategy()
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution) as client:
        created = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "paper",
                "paper_starting_cash": "10000",
            },
        )

    assert created.status_code == 201
    body = created.json()
    products = [item["product_id"] for item in body["instrument_runtimes"]]
    assert products == ["BTC-USD", "ETH-USD"]
    assert {item["phase"] for item in body["instrument_runtimes"]} == {"flat"}
    assert body["positions"] == []
    assert body["position"] is None
    assert body["book_totals"] == {"open_books": 0, "working_orders": 0, "fill_count": 0}


def test_primary_flat_secondary_open_is_labeled_on_api() -> None:
    """N02: a sole secondary book is compatibility focus and still tagged ETH-USD."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _two_product_strategy()
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution) as client:
        created = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "paper",
                "paper_starting_cash": "10000",
            },
        )
        deployment_id = UUID(created.json()["id"])
        eth = _open_position(
            deployment_id=deployment_id,
            product_id="ETH-USD",
            quantity="0.5",
            side=PositionSide.SHORT,
            entry_price="3000",
        )
        order = _seed_order(
            deployment_id=deployment_id,
            product_id="ETH-USD",
            side=OrderSide.SELL,
            status=OrderStatus.FILLED,
            quantity="0.5",
            price="3000",
            filled_quantity="0.5",
        )
        fill = Fill(
            id=uuid7(_at(1)),
            deployment_id=deployment_id,
            order_id=order.id,
            venue_fill_id="eth-open",
            price=Decimal("3000"),
            quantity=Decimal("0.5"),
            fee=Decimal("0"),
            filled_at=_at(1),
            economics_applied_at=_at(1),
        )
        asyncio.run(execution.save_position(eth, deployment_id=deployment_id))
        asyncio.run(execution.save_order(order))
        asyncio.run(execution.save_fill(fill))
        fetched = client.get(f"/api/v1/deployments/{deployment_id}?detail=full")
        summary = client.get(f"/api/v1/deployments/{deployment_id}")

    assert fetched.status_code == 200
    body = fetched.json()
    # ADR 0097: an open paper book is open and protected; the phase stays raw.
    assert (body["position_state"], body["exit_in_flight"]) == ("open_protected", False)
    # Bounded summaries retain local position cover, but omit full economic evidence.
    assert (summary.json()["position_state"], summary.json()["exit_in_flight"]) == (
        "open_unverified",
        False,
    )
    for read in (body, summary.json()):
        assert read["positions"][0]["position_state"] == "open_protected"
        assert read["positions"][0]["exit_in_flight"] is False
    assert body["product_id"] == "BTC-USD"
    assert [item["product_id"] for item in body["positions"]] == ["ETH-USD"]
    assert body["positions"][0]["side"] == "short"
    assert body["positions"][0]["quantity"] == "0.5"
    assert body["position"]["product_id"] == "ETH-USD"
    assert body["position"]["compatibility_focus"] is True
    runtimes = {item["product_id"]: item["phase"] for item in body["instrument_runtimes"]}
    assert runtimes["BTC-USD"] == "flat"
    assert runtimes["ETH-USD"] == "open"
    assert body["orders"][0]["product_id"] == "ETH-USD"
    assert body["fills"][0]["product_id"] == "ETH-USD"
    assert body["book_totals"]["open_books"] == 1
    assert body["book_totals"]["fill_count"] == 1
    assert body["book_totals"]["open_books"] == len(body["positions"])
    assert body["book_totals"]["fill_count"] == len(body["fills"])


def test_two_open_books_keep_distinct_sides_and_reconcile_totals() -> None:
    """Primary long and secondary short remain unambiguous; totals match collections."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _two_product_strategy()
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution) as client:
        created = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "paper",
                "paper_starting_cash": "10000",
            },
        )
        deployment_id = UUID(created.json()["id"])
        btc = _open_position(
            deployment_id=deployment_id,
            product_id="BTC-USD",
            quantity="0.01",
            side=PositionSide.LONG,
            entry_price="100",
        )
        eth = _open_position(
            deployment_id=deployment_id,
            product_id="ETH-USD",
            quantity="0.5",
            side=PositionSide.SHORT,
            entry_price="3000",
        )
        btc_order = _seed_order(
            deployment_id=deployment_id,
            product_id="BTC-USD",
            side=OrderSide.BUY,
            status=OrderStatus.OPEN,
            quantity="0.01",
            price="100",
        )
        eth_order = _seed_order(
            deployment_id=deployment_id,
            product_id="ETH-USD",
            side=OrderSide.SELL,
            status=OrderStatus.FILLED,
            quantity="0.5",
            price="3000",
            filled_quantity="0.5",
        )
        eth_fill = Fill(
            id=uuid7(_at(1)),
            deployment_id=deployment_id,
            order_id=eth_order.id,
            venue_fill_id="eth-open",
            price=Decimal("3000"),
            quantity=Decimal("0.5"),
            fee=Decimal("0"),
            filled_at=_at(1),
        )
        asyncio.run(execution.save_position(btc, deployment_id=deployment_id))
        asyncio.run(execution.save_position(eth, deployment_id=deployment_id))
        asyncio.run(execution.save_order(btc_order))
        asyncio.run(execution.save_order(eth_order))
        asyncio.run(execution.save_fill(eth_fill))
        fetched = client.get(f"/api/v1/deployments/{deployment_id}?detail=full")

    assert fetched.status_code == 200
    body = fetched.json()
    by_product = {item["product_id"]: item for item in body["positions"]}
    assert set(by_product) == {"BTC-USD", "ETH-USD"}
    assert by_product["BTC-USD"]["side"] == "long"
    assert by_product["BTC-USD"]["quantity"] == "0.01"
    assert by_product["ETH-USD"]["side"] == "short"
    assert by_product["ETH-USD"]["quantity"] == "0.5"
    assert body["position"]["product_id"] == "BTC-USD"
    assert body["position"]["compatibility_focus"] is True
    assert by_product["ETH-USD"]["compatibility_focus"] is False
    order_products = {item["product_id"] for item in body["orders"]}
    assert order_products == {"BTC-USD", "ETH-USD"}
    assert body["book_totals"]["open_books"] == 2
    assert body["book_totals"]["working_orders"] == 1
    assert body["book_totals"]["fill_count"] == 1
    assert body["book_totals"]["open_books"] == len(body["positions"])
    assert body["book_totals"]["fill_count"] == len(body["fills"])
    working = sum(1 for item in body["orders"] if item["status"] in {"pending", "open", "unknown"})
    assert body["book_totals"]["working_orders"] == working


def test_deployment_response_includes_capital_accounting_block() -> None:
    """Capital fields are exposed separately from ledger cash for agent and UI review."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )
    deployment_id = uuid4()
    now = datetime(2026, 9, 17, tzinfo=UTC)
    created = asyncio.run(
        execution.create_deployment(
            Deployment(
                id=deployment_id,
                strategy_fingerprint=fingerprint,
                strategy_id=definition.strategy_id,
                product_id="BTC-USD",
                mode=DeploymentMode.LIVE,
                status=DeploymentStatus.RUNNING,
                cash=Decimal("0"),
                phase=RuntimePhase.FLAT,
                created_at=now,
                updated_at=now,
                allocated_capital=Decimal("25000"),
                venue_available_quote=Decimal("50000"),
                reserved_buying_power=Decimal("1200"),
                inventory_cost=Decimal("800"),
                performance_equity=Decimal("24800"),
                initial_equity=Decimal("25000"),
                baseline_equity=Decimal("25000"),
                high_water_mark_equity=Decimal("25200"),
                utc_day_open_equity=Decimal("24900"),
            )
        )
    )
    assert created.id == deployment_id

    with _client(publication, execution) as client:
        fetched = client.get(f"/api/v1/deployments/{deployment_id}?detail=full")

    assert fetched.status_code == 200
    body = fetched.json()
    assert body["cash"] == "0"
    capital = body["capital"]
    assert capital["allocated_capital"] == "25000"
    assert capital["venue_available_quote"] == "50000"
    assert capital["reserved_buying_power"] == "1200"
    assert capital["inventory_cost"] == "800"
    assert capital["performance_equity"] == "24800"
    assert capital["initial_equity"] == "25000"
    assert capital["utc_day_open_equity"] == "24900"


def test_deployment_records_strategy_name_and_filters_by_strategy() -> None:
    """A started bot names its strategy and the list filters by strategy_id."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )
    with _client(publication, execution) as client:
        created = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(definition.strategy_id),
                "mode": "paper",
                "paper_starting_cash": "10000",
            },
        )
        mine = client.get(f"/api/v1/deployments?strategy_id={definition.strategy_id}")
        other = client.get("/api/v1/deployments?strategy_id=01985cf0-7b60-7000-8000-0000000000ee")
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["strategy_fingerprint"] == fingerprint
    assert body["strategy_name"] == definition.name
    assert body["strategy_deleted"] is False
    assert [item["id"] for item in mine.json()["deployments"]] == [body["id"]]
    assert other.json()["deployments"] == []
