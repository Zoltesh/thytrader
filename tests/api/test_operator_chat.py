"""HTTP contract tests for in-app operator chat."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient
from pydantic import SecretStr

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.observability.logging import set_extra_redacted_secrets
from thytrader.operator_chat.models import LlmCompletion, LlmToolCall
from thytrader.persistence.audit_events import InMemoryAuditEventStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.operator_chat.credentials import StoredLlmCredentials


class _ScriptedLlm:
    """Return queued completions without opening a network socket."""

    def __init__(self, completions: list[LlmCompletion]) -> None:
        """Store scripted turns."""
        self._completions = list(completions)
        self.calls = 0

    async def complete(
        self,
        *,
        credentials: StoredLlmCredentials,
        messages: Sequence[dict[str, object]],
        tools: Sequence[dict[str, object]],
    ) -> LlmCompletion:
        """Pop the next scripted turn."""
        del credentials, messages, tools
        if self.calls >= len(self._completions):
            return LlmCompletion(content="done")
        item = self._completions[self.calls]
        self.calls += 1
        return item


_LLM_KEY = "sk-test-operator-chat-secret-key"


def teardown_function() -> None:
    """Do not leak a pasted LLM key into later log tests."""
    set_extra_redacted_secrets(())


def _client(llm: _ScriptedLlm | None = None) -> TestClient:
    """Build an API app with a scripted LLM and no Coinbase keys."""
    app = create_app(
        Settings(_env_file=None),
        audit_event_store=InMemoryAuditEventStore(),
        operator_chat_llm=llm or _ScriptedLlm([]),
    )
    return TestClient(app)


def test_status_omits_key_and_coinbase_fields() -> None:
    """GET status never includes the LLM secret or Coinbase material."""
    with _client() as client:
        saved = client.put(
            "/api/v1/operator-chat/credentials",
            json={"provider": "openai", "model": "gpt-4o-mini", "api_key": _LLM_KEY},
        )
        status = client.get("/api/v1/operator-chat/status")
        transcript = client.get("/api/v1/operator-chat/transcript")
    assert saved.status_code == 200
    body = status.json()
    assert body["schema_version"] == "thytrader-operator-chat-v1"
    assert body["llm_configured"] is True
    assert body["coinbase_credentials_in_chat"] is False
    assert _LLM_KEY not in status.text
    assert "api_key" not in body
    assert "coinbase" not in status.text.lower() or "coinbase_credentials_in_chat" in status.text
    assert _LLM_KEY not in transcript.text


def test_coinbase_fields_are_rejected_on_llm_credential_write() -> None:
    """Extra Coinbase keys are not this surface."""
    with _client() as client:
        response = client.put(
            "/api/v1/operator-chat/credentials",
            json={
                "provider": "openai",
                "model": "gpt-4o-mini",
                "api_key": _LLM_KEY,
                "coinbase_api_key_name": "organizations/example/apiKeys/secret",
            },
        )
    assert response.status_code == 400
    assert "organizations/example" not in response.text
    assert _LLM_KEY not in response.text
    assert "Coinbase" in response.json()["detail"]


def test_coinbase_host_base_url_is_rejected() -> None:
    """LLM base_url cannot point at Coinbase."""
    with _client() as client:
        response = client.put(
            "/api/v1/operator-chat/credentials",
            json={
                "provider": "openai_compatible",
                "model": "gpt-4o-mini",
                "base_url": "https://api.coinbase.com/v1",
                "api_key": _LLM_KEY,
            },
        )
    assert response.status_code == 400
    assert "Coinbase" in response.json()["detail"]


def test_message_without_key_conflicts() -> None:
    """Chat requires a pasted LLM key first."""
    with _client() as client:
        response = client.post("/api/v1/operator-chat/messages", json={"content": "health?"})
    assert response.status_code == 409


def test_read_only_health_tool_runs_without_confirmation() -> None:
    """Operator lane tools execute immediately through the HTTP contract."""
    llm = _ScriptedLlm(
        [
            LlmCompletion(
                content=None,
                tool_calls=(LlmToolCall(id="call_health", name="operator_health", arguments={}),),
            ),
            LlmCompletion(content="API health was read through the operator contract."),
        ]
    )
    with _client(llm) as client:
        client.put(
            "/api/v1/operator-chat/credentials",
            json={"provider": "openai", "model": "gpt-4o-mini", "api_key": _LLM_KEY},
        )
        response = client.post("/api/v1/operator-chat/messages", json={"content": "health?"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["pending_confirmations"] == []
    text = response.text
    assert _LLM_KEY not in text
    assert any("API health was read" in row["content"] for row in payload["messages"])


def test_live_start_stays_pending_without_understand_live() -> None:
    """Live mutations queue confirmation and refuse without the hard gate."""
    fingerprint = "sha256:" + ("ab" * 32)
    llm = _ScriptedLlm(
        [
            LlmCompletion(
                content="Queued live start.",
                tool_calls=(
                    LlmToolCall(
                        id="call_live",
                        name="runtime_start",
                        arguments={"strategy_fingerprint": fingerprint, "mode": "live"},
                    ),
                ),
            )
        ]
    )
    with _client(llm) as client:
        client.put(
            "/api/v1/operator-chat/credentials",
            json={"provider": "openai", "model": "gpt-4o-mini", "api_key": _LLM_KEY},
        )
        queued = client.post(
            "/api/v1/operator-chat/messages",
            json={"content": "start live"},
        )
        assert queued.status_code == 200
        pending = queued.json()["pending_confirmations"]
        assert len(pending) == 1
        assert pending[0]["requires_understand_live"] is True
        confirmation_id = pending[0]["id"]
        refused = client.post(
            f"/api/v1/operator-chat/confirmations/{confirmation_id}",
            json={"confirmed": True, "i_understand_live": False},
        )
        assert refused.status_code == 400
        still = client.get("/api/v1/operator-chat/transcript")
        assert still.json()["pending_confirmations"][0]["id"] == confirmation_id
        assert "arguments" not in still.json()["pending_confirmations"][0]


def test_research_and_memory_mutations_queue_confirmation() -> None:
    """Research and memory writes stay pending; YOLO never covers memory."""
    llm = _ScriptedLlm(
        [
            LlmCompletion(
                content=None,
                tool_calls=(
                    LlmToolCall(
                        id="call_journal",
                        name="memory_add_journal",
                        arguments={
                            "origin": "agent",
                            "kind": "note",
                            "title": "coverage",
                            "body": "watch_complete is the coverage signal",
                        },
                    ),
                ),
            )
        ]
    )
    with _client(llm) as client:
        client.put(
            "/api/v1/operator-chat/credentials",
            json={"provider": "openai", "model": "gpt-4o-mini", "api_key": _LLM_KEY},
        )
        queued = client.post(
            "/api/v1/operator-chat/messages",
            json={"content": "journal that note"},
        )
    assert queued.status_code == 200
    pending = queued.json()["pending_confirmations"]
    assert len(pending) == 1
    assert pending[0]["lane"] == "memory"
    assert pending[0]["requires_understand_live"] is False
    assert pending[0]["tool_name"] == "memory_add_journal"
    assert "arguments" not in pending[0]


def test_coinbase_settings_secret_is_redacted_from_transcript() -> None:
    """Configured Coinbase material never appears in chat payloads."""
    coinbase_name = "organizations/example/apiKeys/chat-test-name"
    llm = _ScriptedLlm([LlmCompletion(content=f"key={coinbase_name}")])
    app = create_app(
        Settings(
            coinbase_api_key_name=SecretStr(coinbase_name),
            coinbase_api_private_key=SecretStr("test-private-key-material"),
            _env_file=None,
        ),
        operator_chat_llm=llm,
    )
    with TestClient(app) as client:
        client.put(
            "/api/v1/operator-chat/credentials",
            json={"provider": "openai", "model": "gpt-4o-mini", "api_key": _LLM_KEY},
        )
        response = client.post("/api/v1/operator-chat/messages", json={"content": "hello"})
    assert response.status_code == 200
    assert coinbase_name not in response.text
    assert _LLM_KEY not in response.text
    assert "test-private-key-material" not in response.text
