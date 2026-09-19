"""Study submission ambiguity readback tests.

A timed-out synchronous ``submit-study`` may have already persisted the study
(derived publications and children included). The CLI must surface the request
fingerprint and the readback command instead of a bare generic failure.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from thytrader.agent_http import AgentHttpError
from thytrader.research.http import _ambiguous_study_error, find_study_by_request
from thytrader.research.studies import ResearchStudyRequest, request_fingerprint


def _study_request() -> ResearchStudyRequest:
    """Return one minimal valid parameter-sweep request."""
    return ResearchStudyRequest.model_validate(
        {
            "kind": "parameter_sweep",
            "evaluation_start": "2026-01-01T00:00:00Z",
            "evaluation_end": "2026-01-11T00:00:00Z",
            "initial_quote_balance": "10000",
            "maker_fee_rate": "0.001",
            "taker_fee_rate": "0.002",
            "fixed_slippage_bps": "10",
            "engine_contract_version": "thytrader-bar-backtest-v1",
            "strategy_fingerprint": "sha256:" + "a" * 64,
            "dataset_fingerprint": "sha256:" + "b" * 64,
            "parameter_axes": [
                {"indicator_id": "fast", "parameter": "period", "values": ["12", "26"]}
            ],
        }
    )


def test_ambiguous_timeout_error_names_request_fingerprint_and_readback() -> None:
    """A timed-out submission must tell the operator how to read back state."""
    request = _study_request()
    error = _ambiguous_study_error(
        request,
        AgentHttpError("HTTP request timed out after 5.0 seconds"),
    )
    message = str(error)
    assert "--request-fingerprint" in message
    assert request_fingerprint(request) in message
    assert "find-study-by-request" in message
    assert "already be persisted" in message


def test_definitive_rejection_names_identity_without_ambiguity_hint() -> None:
    """A 422 rejection is definitive: identity yes, ambiguous readback no."""
    request = _study_request()
    error = _ambiguous_study_error(
        request,
        AgentHttpError("HTTP 422: study_window_rejected"),
    )
    message = str(error)
    assert "422" in message
    assert "--request-fingerprint" in message
    assert request_fingerprint(request) in message
    assert "already be persisted" not in message


def test_unreachable_submission_is_ambiguous_and_names_readback() -> None:
    """A transport-level unreachable failure must keep the readback hint."""
    request = _study_request()
    error = _ambiguous_study_error(
        request,
        AgentHttpError("ThyTrader API is unreachable at http://127.0.0.1:8000."),
    )
    message = str(error)
    assert "--request-fingerprint" in message
    assert "already be persisted" in message


def test_server_timeout_status_is_ambiguous_and_names_readback() -> None:
    """A 504 gateway timeout after a write-risk POST must carry the readback hint."""
    error = _ambiguous_study_error(
        _study_request(),
        AgentHttpError("HTTP 504: gateway timed out"),
    )
    message = str(error)
    assert "504" in message
    assert "--request-fingerprint" in message
    assert "already be persisted" in message


def test_find_study_by_request_returns_matching_row() -> None:
    """The readback command returns the persisted row for one request identity."""
    fingerprint = "sha256:" + "f" * 64
    catalog: dict[str, object] = {
        "studies": [
            {
                "study_fingerprint": fingerprint,
                "request_fingerprint": "sha256:" + "r" * 64,
                "kind": "parameter_sweep",
            }
        ]
    }

    def fake_request_json(*, method: str, url: str, **_kwargs: object) -> dict[str, object]:
        del method
        assert "/api/v1/research/studies" in url
        assert "limit=100" in url
        return catalog

    with patch("thytrader.research.http.request_json", side_effect=fake_request_json):
        payload = json.loads(find_study_by_request("http://127.0.0.1:8000", "sha256:" + "r" * 64))
    assert payload["study_fingerprint"] == fingerprint


def test_find_study_by_request_fails_closed_when_absent() -> None:
    """A missing request identity must say so instead of returning an empty body."""
    catalog: dict[str, object] = {"studies": []}

    def fake_request_json(*, method: str, url: str, **_kwargs: object) -> dict[str, object]:
        del method, url
        return catalog

    with (
        patch("thytrader.research.http.request_json", side_effect=fake_request_json),
        pytest.raises(Exception, match="No persisted study exists"),
    ):
        find_study_by_request("http://127.0.0.1:8000", "sha256:" + "r" * 64)
