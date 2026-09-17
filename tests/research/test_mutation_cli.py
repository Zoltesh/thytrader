"""CLI tests for confirmation-gated research mutations."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Protocol
from unittest.mock import patch
from uuid import UUID

import pytest

from tests.http_fakes import (
    json_urlopen_response,
    matching_ready_payload,
    orchestration_status_payload,
    stale_ready_payload,
    urlopen_by_path,
    urlopen_ready_then,
)
from thytrader.agent_http import AgentHttpError
from thytrader.memory.models import (
    ActorOrigin,
    ExperientialAdvisory,
    ExperientialCorpus,
    ExperientialModel,
    PatternScore,
)
from thytrader.research.mutation_cli import main

_REFERENCE_STRATEGY = (
    Path(__file__).parents[1] / "strategies" / "golden" / "reference_strategy_v1.json"
)
_MODEL_ID = UUID("11111111-1111-1111-1111-111111111111")


class _HasFullUrl(Protocol):
    """urllib Request-shaped object used by the missing-model urlopen double."""

    full_url: str


def test_research_help_mentions_confirm_and_no_trading(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operators can discover the confirmation gate without a database."""
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "--confirm" in output
    assert "no paper or live" in output.lower() or "no paper" in output.lower()


def test_create_draft_help_allows_five_minute_paper(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """create-draft help must name ingested venue clocks for paper and live."""
    with pytest.raises(SystemExit) as raised:
        main(["create-draft", "--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out.lower()
    collapsed = " ".join(output.split())
    assert "any ingested venue clock" in collapsed
    assert "live stays 1h" not in collapsed
    assert "1h or 5m" not in collapsed


def test_create_draft_without_confirm_does_not_write() -> None:
    """Omitting --confirm in Safe mode exits after the YOLO probe, before create-draft HTTP."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(),
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch("thytrader.research.http.create_draft") as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(["create-draft"])
    assert raised.value.code != 0
    assert "Pass --confirm" in str(raised.value)
    request.assert_not_called()


def test_save_draft_help_documents_revision_one_for_new_identities(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """save-draft help must explain that new identities start at revision 1."""
    with pytest.raises(SystemExit) as raised:
        main(["save-draft", "--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out.lower()
    assert "revision 1" in output


def test_import_draft_without_confirm_does_not_write() -> None:
    """Omitting --confirm must exit before import-draft HTTP."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(),
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch("thytrader.research.http.import_draft") as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(["import-draft", "--file", str(_REFERENCE_STRATEGY)])
    assert raised.value.code != 0
    assert "Pass --confirm" in str(raised.value)
    request.assert_not_called()


def test_save_draft_prints_crossover_validation_error(tmp_path: Path) -> None:
    """Invalid crossover operands must surface the semantic validator message."""
    payload = json.loads(_REFERENCE_STRATEGY.read_text())
    payload["entry"]["when"]["all"][0]["right"] = {"literal": "50"}
    path = tmp_path / "draft.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(SystemExit) as raised:
        main(["save-draft", "--file", str(path), "--revision", "1", "--confirm"])
    assert "crossover right operand must reference an indicator" in str(raised.value)
    assert "failed safely" not in str(raised.value)


def test_submit_backtest_stale_engine_422_hints_rebuild(tmp_path: Path) -> None:
    """A stale API that still only names v1/v2 must tell operators to rebuild."""
    path = tmp_path / "request.json"
    path.write_text("{}")
    with (
        patch(
            "thytrader.research.mutation_cli.BacktestSubmissionRequest.model_validate",
            return_value=object(),
        ),
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(matching_ready_payload()),
        ),
        patch(
            "thytrader.research.http.submit_backtest",
            side_effect=AgentHttpError(
                "HTTP 422: Input should be 'thytrader-bar-backtest-v1' or "
                "'thytrader-bar-backtest-v2'"
            ),
        ),
        pytest.raises(SystemExit) as raised,
    ):
        main(["submit-backtest", "--file", str(path), "--confirm"])
    message = str(raised.value)
    assert "422" in message
    assert "make run" in message
    assert "failed safely" not in message


def test_submit_backtest_stale_v3_only_engine_422_hints_rebuild(tmp_path: Path) -> None:
    """A pre-v4 API that lists v1-v3 but not v4 must tell operators to rebuild."""
    path = tmp_path / "request.json"
    path.write_text("{}")
    with (
        patch(
            "thytrader.research.mutation_cli.BacktestSubmissionRequest.model_validate",
            return_value=object(),
        ),
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(matching_ready_payload()),
        ),
        patch(
            "thytrader.research.http.submit_backtest",
            side_effect=AgentHttpError(
                "HTTP 422: Input should be 'thytrader-bar-backtest-v1', "
                "'thytrader-bar-backtest-v2', or 'thytrader-bar-backtest-v3'"
            ),
        ),
        pytest.raises(SystemExit) as raised,
    ):
        main(["submit-backtest", "--file", str(path), "--confirm"])
    message = str(raised.value)
    assert "422" in message
    assert "make run" in message
    assert "failed safely" not in message


def test_create_draft_yolo_skips_confirm() -> None:
    """Research YOLO records a skip then creates a draft without --confirm."""
    skip = {
        "id": "11111111-1111-1111-1111-111111111111",
        "category": "research",
        "action": "confirm_skipped",
        "outcome": "info",
        "tier": "research",
        "command": "create-draft",
    }
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(
            yolo_enabled=True,
            yolo_tiers=("research",),
        ),
        "POST /api/v1/agent-orchestration/skipped-confirmations": skip,
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch(
            "thytrader.research.http.create_draft",
            return_value='{"strategy_id":"x"}',
        ) as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(["create-draft"])
    assert raised.value.code == 0
    request.assert_called_once()


def test_research_cli_refuses_stale_ops_contract_before_command() -> None:
    """Every HTTP research command stops when the ready API contract is stale."""
    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(stale_ready_payload()),
        ),
        patch("thytrader.research.http.create_draft") as request,
        pytest.raises(SystemExit, match="make run"),
    ):
        main(["create-draft", "--confirm"])
    request.assert_not_called()


def test_create_draft_help_lists_research_templates(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operators can discover the Phase 11 template ids without a database."""
    with pytest.raises(SystemExit) as raised:
        main(["create-draft", "--help"])
    assert raised.value.code == 0
    output = " ".join(capsys.readouterr().out.split())
    assert "--template" in output
    assert "macd-trend" in output
    assert "mean-reversion" in output
    assert "bollinger" in output


def test_list_templates_local_does_not_require_a_database(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Template discovery is a read-only catalog, not a mutation."""
    with pytest.raises(SystemExit) as raised:
        main(["--local", "list-templates"])
    assert raised.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    ids = {item["id"] for item in payload["templates"]}
    assert "ema-trend" in ids
    assert "rsi-mean-reversion" in ids


def test_submit_study_without_confirm_does_not_submit() -> None:
    """Omitting --confirm must exit after the YOLO probe, before submit-study HTTP."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(),
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch("thytrader.research.http.submit_study") as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(["submit-study", "--file", "study.json"])
    assert raised.value.code != 0
    assert "Pass --confirm" in str(raised.value)
    request.assert_not_called()


def _trained_model_payload() -> dict[str, object]:
    """Return one valid trained-model JSON document."""
    model = ExperientialModel(
        id=_MODEL_ID,
        recorded_at=datetime(2026, 9, 16, 12, 0, tzinfo=UTC),
        origin=ActorOrigin.AGENT,
        seed=1,
        fingerprint="sha256:" + ("a" * 64),
        corpus=ExperientialCorpus(
            journal_ids=(),
            pattern_ids=("22222222-2222-2222-2222-222222222222",),
            sentiment_ids=(),
            evidence_ids=("backtest:bt-1",),
            human_rows=0,
            agent_rows=1,
        ),
        pattern_scores=(
            PatternScore(
                pattern_key="morning_gap",
                name="Morning gap",
                score=3,
                support_count=1,
                contradict_count=0,
            ),
        ),
        product_scores=(),
        advisory=ExperientialAdvisory(
            suggested_pattern_keys=("morning_gap",),
            caution_pattern_keys=(),
            suggested_products=(),
            caution_products=(),
            notes="Advisory only. Not a live brain.",
        ),
    )
    dumped = model.model_dump(mode="json")
    return {str(key): value for key, value in dumped.items()}


def test_create_draft_local_refuses_experiential_model_id() -> None:
    """Gated advisory input is HTTP-only."""
    with pytest.raises(SystemExit, match="HTTP transport") as raised:
        main(
            [
                "--local",
                "create-draft",
                "--experiential-model-id",
                str(_MODEL_ID),
                "--confirm",
            ]
        )
    assert raised.value.code != 0


def test_create_draft_merges_experiential_advisory(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """HTTP create-draft JSON includes the fail-closed advisory, not a live policy."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        f"GET /api/v1/memory/models/{_MODEL_ID}": _trained_model_payload(),
        "POST /api/v1/strategies": {
            "revision": 1,
            "strategy": {
                "strategy_id": "0199aaaa-aaaa-7aaa-aaaa-aaaaaaaaaaaa",
                "version": 1,
                "name": "EMA trend",
            },
        },
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        pytest.raises(SystemExit) as raised,
    ):
        main(
            [
                "create-draft",
                "--experiential-model-id",
                str(_MODEL_ID),
                "--confirm",
            ]
        )
    assert raised.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["experiential_model_id"] == str(_MODEL_ID)
    assert payload["experiential_advisory"]["suggested_pattern_keys"] == ["morning_gap"]
    assert "live brain" in payload["experiential_advisory"]["notes"].lower()


def test_create_draft_missing_model_does_not_create() -> None:
    """A missing trained model fails closed before POST /strategies."""

    def fake_urlopen(request: _HasFullUrl | str, timeout: object = None) -> object:
        del timeout
        requested_url = request if isinstance(request, str) else request.full_url
        path = str(requested_url)
        if path.endswith("/health/ready"):
            return json_urlopen_response(matching_ready_payload())
        if "/api/v1/memory/models/" in path:
            return json_urlopen_response({"detail": "Model was not found."}, status=404)
        message = f"unexpected agent HTTP request: {path}"
        raise AssertionError(message)

    with (
        patch("thytrader.agent_http.urlopen", side_effect=fake_urlopen),
        pytest.raises(SystemExit) as raised,
    ):
        main(
            [
                "create-draft",
                "--experiential-model-id",
                str(_MODEL_ID),
                "--confirm",
            ]
        )
    assert raised.value.code != 0
    assert "failed safely" not in str(raised.value).lower()


def test_create_draft_help_names_experiential_model(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operators can discover the gated advisory flag from --help."""
    with pytest.raises(SystemExit) as raised:
        main(["create-draft", "--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out.lower()
    assert "--experiential-model-id" in output
    assert "http" in output
