"""HTTP contracts for paper and live strategy deployments."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr

from thytrader.api.app import create_app
from thytrader.config import Environment, Settings
from thytrader.execution.ids import uuid7
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Fill,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    PositionSide,
)
from thytrader.persistence.audit_events import AuditEventCategory, InMemoryAuditEventStore
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.security.models import INSTALLATION_AUTH_HEADER
from thytrader.strategies.authoring import StrategyDraft, create_reference_draft
from thytrader.strategies.models import (
    Instrument,
    StrategyDefinition,
    StrategyStatus,
    strategy_fingerprint,
)
from thytrader.strategies.publication import (
    PublishedStrategy,
    StrategyCatalogEntry,
    StrategyPublicationError,
)


class InMemoryDraftStore:
    """Minimal draft store so create_app can satisfy the authoring boundary."""

    def __init__(self) -> None:
        """Start with no drafts."""
        self.drafts: dict[tuple[str, int], StrategyDraft] = {}

    async def create_draft(self, definition: StrategyDefinition) -> StrategyDraft:
        """Record one draft."""
        draft = StrategyDraft(definition=definition, revision=1)
        self.drafts[(str(definition.strategy_id), definition.version)] = draft
        return draft

    async def list_drafts(self) -> tuple[StrategyDraft, ...]:
        """Return saved drafts."""
        return tuple(self.drafts.values())

    async def save_draft(
        self, definition: StrategyDefinition, *, expected_revision: int
    ) -> StrategyDraft:
        """Replace a current draft."""
        del expected_revision
        draft = StrategyDraft(definition=definition, revision=2)
        self.drafts[(str(definition.strategy_id), definition.version)] = draft
        return draft

    async def delete_draft(self, strategy_id: UUID, version: int) -> None:
        """Remove one draft."""
        self.drafts.pop((str(strategy_id), version), None)


class InMemoryPublicationStore:
    """Load published strategies by fingerprint for deployment tests."""

    def __init__(self) -> None:
        """Start without publications."""
        self.published: dict[str, PublishedStrategy] = {}

    async def publish(self, definition: StrategyDefinition) -> PublishedStrategy:
        """Retain one immutable publication."""
        fingerprint = strategy_fingerprint(definition)
        published = PublishedStrategy(strategy_fingerprint=fingerprint, definition=definition)
        self.published[fingerprint] = published
        return published

    async def publish_draft(
        self, definition: StrategyDefinition, *, expected_revision: int
    ) -> PublishedStrategy:
        """Unused atomic publish path for this store."""
        del expected_revision
        return await self.publish(definition)

    async def load(self, strategy_fingerprint_value: str) -> PublishedStrategy:
        """Return one previously published definition or fail."""
        published = self.published.get(strategy_fingerprint_value)
        if published is None:
            raise StrategyPublicationError("Published strategy was not found.")
        return published

    async def list_published(self, *, include_archived: bool) -> tuple[StrategyCatalogEntry, ...]:
        """Return every retained publication."""
        del include_archived
        return tuple(
            StrategyCatalogEntry(
                strategy_fingerprint=fingerprint,
                definition=item.definition,
                archived_at=None,
            )
            for fingerprint, item in self.published.items()
        )

    async def archive(self, strategy_fingerprint_value: str) -> StrategyCatalogEntry:
        """Unused archive path for this store."""
        published = await self.load(strategy_fingerprint_value)
        return StrategyCatalogEntry(
            strategy_fingerprint=published.strategy_fingerprint,
            definition=published.definition,
            archived_at=datetime(2026, 1, 2, tzinfo=UTC),
        )


def _published_strategy() -> StrategyDefinition:
    """Return the reference strategy marked published."""
    draft = create_reference_draft(now=datetime(2026, 1, 1, tzinfo=UTC))
    return StrategyDefinition.model_validate(
        {**draft.model_dump(mode="python"), "status": StrategyStatus.PUBLISHED.value}
    )


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
        strategy_draft_store=InMemoryDraftStore(),
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


def test_paper_deployment_persists_and_updates_library_status() -> None:
    """POST paper starts a running deployment and the library reports that status."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)

    with _client(publication, execution) as client:
        publication.published[fingerprint] = PublishedStrategy(
            strategy_fingerprint=fingerprint, definition=definition
        )
        created = client.post(
            "/api/v1/deployments",
            json={
                "strategy_fingerprint": fingerprint,
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
    assert entry["paper_live"] == {"paper": "running", "live": "unavailable"}


def test_paper_deployment_mutation_requires_installation_auth_when_boundary_enabled() -> None:
    """Unauthenticated POST is rejected when ADR 0061 trust boundary is on."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = PublishedStrategy(
        strategy_fingerprint=fingerprint, definition=definition
    )
    token = "0060-boundary-token"
    settings = Settings(
        environment=Environment.TEST,
        installation_token=SecretStr(token),
        trust_boundary_enabled=True,
        _env_file=None,
    )
    app = create_app(
        settings,
        strategy_store=publication,
        strategy_draft_store=InMemoryDraftStore(),
        execution_store=execution,
    )
    payload = {
        "strategy_fingerprint": fingerprint,
        "mode": "paper",
        "paper_starting_cash": "10000",
    }
    with TestClient(app) as client:
        denied = client.post("/api/v1/deployments", json=payload)
        allowed = client.post(
            "/api/v1/deployments",
            json=payload,
            headers={INSTALLATION_AUTH_HEADER: f"Bearer {token}"},
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
    publication.published[fingerprint] = PublishedStrategy(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution) as client:
        created = client.post(
            "/api/v1/deployments",
            json={
                "strategy_fingerprint": fingerprint,
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


def test_duplicate_running_paper_deployment_conflicts() -> None:
    """Only one running paper deployment is allowed per strategy."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = PublishedStrategy(
        strategy_fingerprint=fingerprint, definition=definition
    )
    payload = {
        "strategy_fingerprint": fingerprint,
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
    publication.published[fingerprint] = PublishedStrategy(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution) as client:
        denied = client.post(
            "/api/v1/deployments",
            json={"strategy_fingerprint": fingerprint, "mode": "live"},
        )

    with _client(
        publication, execution, live_credentials=True, risk=_published_risk_policy_store()
    ) as client:
        allowed = client.post(
            "/api/v1/deployments",
            json={"strategy_fingerprint": fingerprint, "mode": "live"},
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
    publication.published[fingerprint] = PublishedStrategy(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution) as client:
        paper = client.post(
            "/api/v1/deployments",
            json={
                "strategy_fingerprint": fingerprint,
                "mode": "paper",
                "paper_starting_cash": "10000",
            },
        )
        live = client.post(
            "/api/v1/deployments",
            json={"strategy_fingerprint": fingerprint, "mode": "live"},
        )

    with _client(
        publication, execution, live_credentials=True, risk=_published_risk_policy_store()
    ) as client:
        live_with_keys = client.post(
            "/api/v1/deployments",
            json={"strategy_fingerprint": fingerprint, "mode": "live"},
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
    publication.published[fingerprint] = PublishedStrategy(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution) as client:
        paper = client.post(
            "/api/v1/deployments",
            json={
                "strategy_fingerprint": fingerprint,
                "mode": "paper",
                "paper_starting_cash": "10000",
            },
        )
        live = client.post(
            "/api/v1/deployments",
            json={"strategy_fingerprint": fingerprint, "mode": "live"},
        )

    with _client(
        publication, execution, live_credentials=True, risk=_published_risk_policy_store()
    ) as client:
        live_with_keys = client.post(
            "/api/v1/deployments",
            json={"strategy_fingerprint": fingerprint, "mode": "live"},
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
    publication.published[fingerprint] = PublishedStrategy(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution) as client:
        paper = client.post(
            "/api/v1/deployments",
            json={
                "strategy_fingerprint": fingerprint,
                "mode": "paper",
                "paper_starting_cash": "10000",
            },
        )

    assert paper.status_code == 201
    assert paper.json()["mode"] == "paper"


def test_unknown_fingerprint_is_not_found() -> None:
    """Deploying an unpublished fingerprint fails closed."""
    with _client(InMemoryPublicationStore(), InMemoryExecutionStore()) as client:
        missing = client.post(
            "/api/v1/deployments",
            json={
                "strategy_fingerprint": "sha256:" + "a" * 64,
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
    eth = create_reference_draft(now=datetime(2026, 1, 2, tzinfo=UTC), product_id="ETH-USD")
    eth = StrategyDefinition.model_validate(
        {**eth.model_dump(mode="python"), "status": StrategyStatus.PUBLISHED.value}
    )
    btc_fp = strategy_fingerprint(btc)
    eth_fp = strategy_fingerprint(eth)
    publication.published[btc_fp] = PublishedStrategy(strategy_fingerprint=btc_fp, definition=btc)
    publication.published[eth_fp] = PublishedStrategy(strategy_fingerprint=eth_fp, definition=eth)

    with _client(publication, execution) as client:
        first = client.post(
            "/api/v1/deployments",
            json={
                "strategy_fingerprint": btc_fp,
                "mode": "paper",
                "paper_starting_cash": "10000",
            },
        )
        second = client.post(
            "/api/v1/deployments",
            json={
                "strategy_fingerprint": eth_fp,
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
    publication.published[fingerprint] = PublishedStrategy(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution) as client:
        denied = client.post(
            "/api/v1/deployments",
            json={
                "strategy_fingerprint": fingerprint,
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
    publication.published[fingerprint] = PublishedStrategy(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution, audit=audit) as client:
        created = client.post(
            "/api/v1/deployments",
            json={
                "strategy_fingerprint": fingerprint,
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
        publication.published[fingerprint] = PublishedStrategy(
            strategy_fingerprint=fingerprint, definition=definition
        )
        return publication, InMemoryExecutionStore()

    publication, execution = _fresh()
    with _client(publication, execution) as client:
        created = client.post(
            "/api/v1/deployments",
            json={
                "strategy_fingerprint": fingerprint,
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
                "strategy_fingerprint": fingerprint,
                "mode": "paper",
                "paper_starting_cash": "10000",
                "maker_fee_rate": "0.0025",
            },
        )
        inverted = client.post(
            "/api/v1/deployments",
            json={
                "strategy_fingerprint": fingerprint,
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
                "strategy_fingerprint": fingerprint,
                "mode": "live",
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
    publication.published[fingerprint] = PublishedStrategy(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution) as client:
        created = client.post(
            "/api/v1/deployments",
            json={
                "strategy_fingerprint": fingerprint,
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
    publication.published[fingerprint] = PublishedStrategy(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution) as client:
        created = client.post(
            "/api/v1/deployments",
            json={
                "strategy_fingerprint": fingerprint,
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
        )
        asyncio.run(execution.save_position(eth, deployment_id=deployment_id))
        asyncio.run(execution.save_order(order))
        asyncio.run(execution.save_fill(fill))
        fetched = client.get(f"/api/v1/deployments/{deployment_id}")

    assert fetched.status_code == 200
    body = fetched.json()
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
    publication.published[fingerprint] = PublishedStrategy(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution) as client:
        created = client.post(
            "/api/v1/deployments",
            json={
                "strategy_fingerprint": fingerprint,
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
        fetched = client.get(f"/api/v1/deployments/{deployment_id}")

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
