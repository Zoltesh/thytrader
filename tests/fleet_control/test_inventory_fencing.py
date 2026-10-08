"""Inventory deletion/reclassification fences and truthful complete CLI walks."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from tests.fleet_control.test_inventory_and_controls import _book
from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.runtime_control.cli import _parser
from thytrader.runtime_control.client import RuntimeControlError, list_deployments
from thytrader.runtime_control.inventory_commands import run_inventory_read
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentKind


@pytest.mark.parametrize("change", ["delete", "reclassify"])
def test_complete_cli_refuses_membership_change_between_pages(
    change: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An HTTP continuation conflict never prints a prefix as a complete fleet."""
    store = InMemoryExecutionStore()
    tied = datetime(2026, 10, 1, tzinfo=UTC)
    rows = [replace(_book(index), created_at=tied) for index in range(61)]
    store.deployments.update({row.id: row for row in rows})
    app = create_app(Settings(_env_file=None), execution_store=store)
    with TestClient(app) as http:
        calls = 0

        def read(*, method: str, url: str) -> object:
            """Route client reads hermetically, then delete/reclassify after page one."""
            nonlocal calls
            path = urlsplit(url)
            response = http.request(method, f"{path.path}?{path.query}")
            calls += 1
            if calls == 1:
                if change == "delete":
                    del store.deployments[rows[-1].id]
                else:
                    store.deployments[rows[-1].id] = replace(rows[-1], kind=DeploymentKind.STRATEGY)
            if response.status_code != 200:
                raise RuntimeControlError(str(response.json()["detail"]))
            return response.json()

        monkeypatch.setattr("thytrader.runtime_control.client.request_json", read)
        with pytest.raises(RuntimeControlError, match="inventory_changed"):
            list_deployments("http://127.0.0.1:8200", page_size=50)
        assert calls == 2


def test_complete_cli_walks_ties_without_skipping_or_duplicating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keyset ordering includes the UUID tie-breaker, not moving updated_at."""
    store = InMemoryExecutionStore()
    tied = datetime(2026, 10, 1, tzinfo=UTC)
    rows = [replace(_book(index), created_at=tied) for index in range(61)]
    store.deployments.update({row.id: row for row in rows})
    app = create_app(Settings(_env_file=None), execution_store=store)
    with TestClient(app) as http:
        calls = 0

        def read(*, method: str, url: str) -> object:
            """Change updated_at after each page; the identity fence remains unchanged."""
            nonlocal calls
            path = urlsplit(url)
            response = http.request(method, f"{path.path}?{path.query}")
            calls += 1
            store.deployments[rows[0].id] = replace(rows[0], updated_at=datetime.now(UTC))
            assert response.status_code == 200
            return response.json()

        monkeypatch.setattr("thytrader.runtime_control.client.request_json", read)
        payload = list_deployments("http://127.0.0.1:8200", page_size=50)
        assert isinstance(payload, dict)
        body = {str(key): value for key, value in payload.items()}
        assert body["complete"] is True
        assert body["returned"] == 61
        assert calls == 2


@pytest.mark.parametrize(
    "anomaly", ["duplicate", "fence", "missing_cursor", "wrong_total", "repeat_cursor"]
)
def test_complete_cli_rejects_pagination_anomalies(
    anomaly: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken or legacy response cannot certify a complete inventory."""
    calls = 0

    def read(*, method: str, url: str) -> object:
        """Return narrowly malformed pagination metadata at the dynamic HTTP boundary."""
        nonlocal calls
        assert method == "GET" and url.startswith("http://127.0.0.1:8200/")
        calls += 1
        body: dict[str, object] = {
            "deployments": [{"id": str(calls)}],
            "returned": 1,
            "has_more": calls == 1,
            "total": 2,
            "fingerprint": "fixed",
            "order": "created_at_desc_id_desc",
            "as_of": "2026-10-06T00:00:00Z",
            "next_cursor": "next" if calls == 1 else None,
        }
        if calls == 2:
            if anomaly == "duplicate":
                body["deployments"] = [{"id": "1"}]
            elif anomaly == "fence":
                body["fingerprint"] = "changed"
            elif anomaly == "missing_cursor":
                del body["next_cursor"]
            elif anomaly == "wrong_total":
                body["total"] = 3
            else:
                body["has_more"] = True
                body["next_cursor"] = "next"
        return body

    monkeypatch.setattr("thytrader.runtime_control.client.request_json", read)
    with pytest.raises(RuntimeControlError):
        list_deployments("http://127.0.0.1:8200", page_size=1)


def test_unfenced_offset_tail_never_claims_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exhausted offset page is a page, not proof of the entire fleet."""
    from_cli = _parser().parse_args(["list", "--limit", "50", "--offset", "50"])

    def page(base_url: str, *, limit: int, offset: int) -> object:
        """One empty tail; this says nothing about rows before the offset."""
        assert base_url == "http://127.0.0.1:8200" and limit == 50 and offset == 50
        return {"deployments": [], "returned": 0, "has_more": False}

    monkeypatch.setattr("thytrader.runtime_control.inventory_commands.list_deployments", page)
    payload = run_inventory_read(from_cli, "http://127.0.0.1:8200")
    assert isinstance(payload, dict)
    body = {str(key): value for key, value in payload.items()}
    assert body["complete"] is False
    assert body["page_complete"] is True
