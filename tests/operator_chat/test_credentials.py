"""Operator-chat credential and catalog tests."""

from __future__ import annotations

from pydantic import SecretStr
import pytest

from thytrader.observability.logging import set_extra_redacted_secrets
from thytrader.operator_chat.credentials import (
    OperatorChatCredentialError,
    OperatorChatCredentialStore,
)
from thytrader.operator_chat.models import (
    ChatRole,
    LlmProvider,
    LlmToolCall,
    OperatorChatCredentialWrite,
)
from thytrader.operator_chat.session import OperatorChatSessionStore
from thytrader.operator_chat.tools import chat_tools, split_request, tool_by_name


def teardown_function() -> None:
    """Do not leak a pasted LLM key into later log tests."""
    set_extra_redacted_secrets(())


def test_credential_store_never_puts_the_key_on_status() -> None:
    """Status payloads omit the secret after a successful paste."""
    store = OperatorChatCredentialStore()
    write = OperatorChatCredentialWrite(
        provider=LlmProvider.OPENAI,
        model="gpt-4o-mini",
        api_key=SecretStr("sk-test-stored-key-value"),
    )
    status = store.replace(write)
    assert status.llm_configured is True
    assert status.coinbase_credentials_in_chat is False
    dumped = status.model_dump()
    assert "api_key" not in dumped
    assert "sk-test-stored-key-value" not in str(dumped)
    stored = store.get()
    assert stored is not None
    assert stored.api_key.get_secret_value() == "sk-test-stored-key-value"


def test_pem_shaped_secret_is_rejected() -> None:
    """A Coinbase PEM must not be accepted as an LLM key."""
    store = OperatorChatCredentialStore()
    write = OperatorChatCredentialWrite(
        provider=LlmProvider.OPENAI,
        model="gpt-4o-mini",
        api_key=SecretStr("-----BEGIN EC PRIVATE KEY-----\nabc\n-----END EC PRIVATE KEY-----"),
    )
    with pytest.raises(OperatorChatCredentialError, match="Coinbase"):
        store.replace(write)


def test_create_draft_uses_query_string() -> None:
    """Research create-draft stays on the existing POST query contract."""
    tool = tool_by_name("research_create_draft")
    assert tool is not None
    path, query, body = split_request(
        tool,
        {"product_id": "ETH-USD", "timeframe": "1h", "template": "ema-trend"},
    )
    assert path == "/api/v1/strategies"
    assert query["product_id"] == "ETH-USD"
    assert body is None


def test_operator_read_tools_include_studies_and_trade_reasons() -> None:
    """Operator chat exposes studies and trade-reason review routes."""
    assert tool_by_name("operator_studies") is not None
    assert tool_by_name("operator_trade_reasons") is not None


def test_runtime_stop_forwards_flatten_query() -> None:
    """Flatten stop uses the HTTP query flag, not a JSON body."""
    tool = tool_by_name("runtime_stop")
    assert tool is not None
    path, query, body = split_request(
        tool,
        {"deployment_id": "00000000-0000-0000-0000-000000000001", "flatten": True},
    )
    assert path == "/api/v1/deployments/00000000-0000-0000-0000-000000000001/stop"
    assert query == {"flatten": "true"}
    assert body is None


def test_catalog_keeps_lanes_separated() -> None:
    """Runtime mutations are not on the operator lane."""
    by_lane = {tool.name: tool.lane.value for tool in chat_tools()}
    assert by_lane["operator_health"] == "operator"
    assert by_lane["data_ingest"] == "data"
    assert by_lane["research_publish"] == "research"
    assert by_lane["runtime_start"] == "runtime"
    assert by_lane["memory_add_journal"] == "memory"
    runtime = tool_by_name("runtime_start")
    assert runtime is not None
    assert runtime.mutation is True
    assert runtime.live_ack == "when_mode_live"
    memory = tool_by_name("memory_add_journal")
    assert memory is not None
    assert memory.hard_gate is True


def test_runtime_start_and_place_order_forward_paper_fee_fields() -> None:
    """Paper deploy fee fields stay on the existing HTTP body (ADR 0048)."""
    start = tool_by_name("runtime_start")
    assert start is not None
    assert "maker_fee_rate" in start.properties
    assert "taker_fee_rate" in start.properties
    path, query, body = split_request(
        start,
        {
            "strategy_fingerprint": "sha256:" + ("ab" * 32),
            "mode": "paper",
            "maker_fee_rate": "0.0025",
            "taker_fee_rate": "0.004",
        },
    )
    assert path == "/api/v1/deployments"
    assert query == {}
    assert body == {
        "strategy_fingerprint": "sha256:" + ("ab" * 32),
        "mode": "paper",
        "maker_fee_rate": "0.0025",
        "taker_fee_rate": "0.004",
    }
    place = tool_by_name("runtime_place_order")
    assert place is not None
    assert "maker_fee_rate" in place.properties
    assert "taker_fee_rate" in place.properties


def test_memory_train_tools_map_onto_adr_0049_http() -> None:
    """Experiential trainer routes stay on the memory lane and stay hard-gated."""
    listed = tool_by_name("memory_list_models")
    assert listed is not None
    assert listed.mutation is False
    assert listed.path == "/api/v1/memory/models"
    shown = tool_by_name("memory_show_model")
    assert shown is not None
    path, _query, body = split_request(shown, {"model_id": "11111111-1111-1111-1111-111111111111"})
    assert path == "/api/v1/memory/models/11111111-1111-1111-1111-111111111111"
    assert body is None
    train = tool_by_name("memory_train")
    assert train is not None
    assert train.lane.value == "memory"
    assert train.mutation is True
    assert train.hard_gate is True
    train_path, train_query, train_body = split_request(train, {"origin": "agent", "seed": 1})
    assert train_path == "/api/v1/memory/models"
    assert train_query == {}
    assert train_body == {"origin": "agent", "seed": 1}


def test_llm_messages_round_trip_assistant_tool_calls() -> None:
    """Provider resumes need the assistant tool_calls block, not only tool rows."""
    session = OperatorChatSessionStore()
    session.add_message(
        role=ChatRole.ASSISTANT,
        content="",
        tool_calls=(LlmToolCall(id="call_health", name="operator_health", arguments={}),),
    )
    session.add_message(
        role=ChatRole.TOOL,
        content="{}",
        tool_call_id="call_health",
        tool_name="operator_health",
    )
    serialized = session.llm_messages()
    assert serialized[0]["role"] == "assistant"
    tool_calls = serialized[0]["tool_calls"]
    assert isinstance(tool_calls, list)
    first_call = tool_calls[0]
    assert isinstance(first_call, dict)
    assert first_call.get("id") == "call_health"
    assert serialized[1]["role"] == "tool"
    assert serialized[1]["tool_call_id"] == "call_health"
