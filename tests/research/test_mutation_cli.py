"""CLI tests for confirmation-gated research mutations."""

from __future__ import annotations

from contextlib import redirect_stdout
from datetime import UTC, datetime
import io
import json
from pathlib import Path
from typing import Protocol, cast
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
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
from thytrader.operator.status import EXIT_HEALTHY
from thytrader.research.mutation_cli import main

_REFERENCE_STRATEGY = (
    Path(__file__).parents[1] / "strategies" / "golden" / "reference_strategy_v1.json"
)
_MODEL_ID = UUID("11111111-1111-1111-1111-111111111111")


_STRATEGY_ID = "0199aaaa-aaaa-7aaa-aaaa-aaaaaaaaaaaa"


def _library_payload() -> dict[str, object]:
    """Return a strategy-library body with one valid and one work-in-progress strategy."""
    return {
        "strategies": [
            {
                "strategy_id": "22222222-2222-2222-2222-222222222222",
                "name": "EMA trend",
                "revision": 3,
                "valid": True,
                "current_fingerprint": "sha256:" + "c" * 64,
                "paper_live": {"paper": "running", "live": "none"},
                "active_deployment_count": 1,
            },
            {
                "strategy_id": "33333333-3333-3333-3333-333333333333",
                "name": "RSI work in progress",
                "revision": 1,
                "valid": False,
                "current_fingerprint": None,
                "paper_live": {"paper": "none", "live": "none"},
                "active_deployment_count": 0,
            },
        ],
        "total": 2,
        "has_more": False,
    }


def _strategy_response(name: str = "EMA trend") -> dict[str, object]:
    """Return one StrategyResponse body."""
    return {
        "strategy_id": _STRATEGY_ID,
        "name": name,
        "revision": 1,
        "validation": {"valid": True, "issues": []},
        "current_fingerprint": "sha256:" + "c" * 64,
        "summary": "BTC-USD · 1h",
    }


def test_delete_without_confirm_does_not_write() -> None:
    """Omitting --confirm must exit before the DELETE call."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(),
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch("thytrader.research.http.delete_strategy") as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(["delete-strategy", "--strategy-id", _STRATEGY_ID])
    assert raised.value.code != 0
    assert "Pass --confirm" in str(raised.value)
    request.assert_not_called()


def test_delete_rejects_a_non_uuid_strategy_id() -> None:
    """A malformed id fails closed before any mutation."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(),
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        pytest.raises(SystemExit) as raised,
    ):
        main(["delete-strategy", "--strategy-id", "not-a-uuid", "--confirm"])
    assert raised.value.code != 0
    assert "UUID" in str(raised.value)


def test_bulk_delete_dry_run_needs_no_confirm_and_reports_per_strategy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A dry run previews each strategy without --confirm and without deleting."""
    body = {
        "dry_run": True,
        "results": [
            {"strategy_id": _STRATEGY_ID, "outcome": "would_delete"},
            {"strategy_id": "22222222-2222-2222-2222-222222222222", "outcome": "blocked"},
        ],
    }
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "POST /api/v1/strategies/bulk-delete": body,
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        pytest.raises(SystemExit) as raised,
    ):
        main(
            [
                "bulk-delete-strategies",
                "--strategy-id",
                _STRATEGY_ID,
                "--strategy-id",
                "22222222-2222-2222-2222-222222222222",
                "--dry-run",
            ]
        )
    assert raised.value.code == EXIT_HEALTHY
    payload = json.loads(capsys.readouterr().out)
    assert [item["outcome"] for item in payload["results"]] == ["would_delete", "blocked"]


def test_bulk_delete_by_tag_pages_the_library_and_batches_by_100(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--tag`` resolves every tagged strategy, then previews them in server batches."""
    identities = [str(UUID(int=index + 1)) for index in range(150)]
    pages = {
        None: {
            "strategies": [
                {"strategy_id": item, "tags": ["per-market"]} for item in identities[:100]
            ],
            "has_more": True,
            "next_cursor": "c100",
        },
        "c100": {
            "strategies": [
                {"strategy_id": item, "tags": ["per-market"]} for item in identities[100:]
            ],
            "has_more": False,
            "next_cursor": None,
        },
    }
    reads: list[str] = []
    batches: list[list[str]] = []

    def fake_read(*, method: str, url: str, **_kw: object) -> object:
        assert method == "GET"
        reads.append(url)
        query = parse_qs(urlparse(url).query)
        assert query["tag"] == ["per-market"]
        return pages[query.get("cursor", [None])[0]]

    def fake_mutation(
        *, method: str, url: str, payload: dict[str, object], **_kw: object
    ) -> object:
        assert method == "POST"
        assert url.endswith("/api/v1/strategies/bulk-delete")
        assert payload["dry_run"] is True
        sent = cast("list[str]", payload["strategy_ids"])
        batches.append(sent)
        return {
            "dry_run": True,
            "results": [{"strategy_id": item, "outcome": "would_delete"} for item in sent],
            "deleted": 0,
            "would_delete": len(sent),
            "blocked": 0,
            "not_found": 0,
            "failed": 0,
        }

    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(matching_ready_payload()),
        ),
        patch("thytrader.research.http_strategies.request_json", side_effect=fake_read),
        patch(
            "thytrader.research.http_strategies.request_mutation_json", side_effect=fake_mutation
        ),
        pytest.raises(SystemExit) as raised,
    ):
        main(["bulk-delete-strategies", "--tag", "per-market", "--dry-run"])
    assert raised.value.code == EXIT_HEALTHY
    payload = json.loads(capsys.readouterr().out)
    assert payload["tag"] == "per-market"
    assert payload["matched"] == 150
    assert payload["would_delete"] == 150
    assert [len(batch) for batch in batches] == [100, 50]
    assert len(reads) == 2


def test_bulk_delete_by_tag_fails_closed_when_the_api_ignores_the_tag() -> None:
    """A stale API that lists untagged strategies deletes nothing."""

    def fake_read(*, method: str, url: str, **_kw: object) -> object:
        del method, url
        return {"strategies": [{"strategy_id": _STRATEGY_ID}], "has_more": False}

    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(matching_ready_payload()),
        ),
        patch("thytrader.research.http_strategies.request_json", side_effect=fake_read),
        patch("thytrader.research.http_strategies.request_mutation_json") as mutation,
        pytest.raises(SystemExit) as raised,
    ):
        main(["bulk-delete-strategies", "--tag", "per-market", "--confirm"])
    assert "nothing was deleted" in str(raised.value)
    mutation.assert_not_called()


def test_bulk_delete_takes_ids_or_a_tag_not_both() -> None:
    """The two target forms are mutually exclusive."""
    with pytest.raises(SystemExit) as raised:
        main(["bulk-delete-strategies", "--tag", "x", "--strategy-id", _STRATEGY_ID, "--dry-run"])
    assert raised.value.code != EXIT_HEALTHY


def test_clone_strategy_name_is_sent_in_the_same_call(capsys: pytest.CaptureFixture[str]) -> None:
    """``clone-strategy --name`` names the copy without a follow-up save."""
    sent: list[object] = []

    def fake_mutation(*, method: str, url: str, payload: object = None, **_kw: object) -> object:
        assert method == "POST"
        assert url.endswith(f"/api/v1/strategies/{_STRATEGY_ID}/clone")
        sent.append(payload)
        return _strategy_response(name="EMA ETH-USDC 1h")

    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(matching_ready_payload()),
        ),
        patch(
            "thytrader.research.http_strategies.request_mutation_json", side_effect=fake_mutation
        ),
        pytest.raises(SystemExit) as raised,
    ):
        main(
            [
                "clone-strategy",
                "--strategy-id",
                _STRATEGY_ID,
                "--name",
                "EMA ETH-USDC 1h",
                "--confirm",
            ]
        )
    assert raised.value.code == EXIT_HEALTHY
    assert sent == [{"name": "EMA ETH-USDC 1h"}]
    assert json.loads(capsys.readouterr().out)["name"] == "EMA ETH-USDC 1h"


def test_list_strategies_tag_filters_on_the_server(capsys: pytest.CaptureFixture[str]) -> None:
    """``list-strategies --tag`` sends ``tag`` and prints each row's tags."""
    urls: list[str] = []

    def fake_read(*, method: str, url: str, **_kw: object) -> object:
        del method
        urls.append(url)
        return {
            "strategies": [{"strategy_id": _STRATEGY_ID, "name": "a", "tags": ["majors"]}],
            "total": 1,
            "has_more": False,
        }

    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(matching_ready_payload()),
        ),
        patch("thytrader.research.http_strategies.request_json", side_effect=fake_read),
        pytest.raises(SystemExit) as raised,
    ):
        main(["list-strategies", "--tag", "majors"])
    assert raised.value.code == EXIT_HEALTHY
    assert parse_qs(urlparse(urls[0]).query)["tag"] == ["majors"]
    assert json.loads(capsys.readouterr().out)["strategies"][0]["tags"] == ["majors"]


def test_list_strategies_origin_filters_on_the_server() -> None:
    """``list-strategies --origin research`` sends ``origin``; the default sends none."""
    urls: list[str] = []

    def fake_read(*, method: str, url: str, **_kw: object) -> object:
        del method
        urls.append(url)
        return {"strategies": [], "total": 0, "has_more": False}

    for argv in (["list-strategies", "--origin", "research"], ["list-strategies"]):
        with (
            patch(
                "thytrader.agent_http.urlopen",
                side_effect=urlopen_ready_then(matching_ready_payload()),
            ),
            patch("thytrader.research.http_strategies.request_json", side_effect=fake_read),
            pytest.raises(SystemExit) as raised,
        ):
            main(argv)
        assert raised.value.code == EXIT_HEALTHY
    assert parse_qs(urlparse(urls[0]).query)["origin"] == ["research"]
    assert "origin" not in parse_qs(urlparse(urls[1]).query)


def test_bulk_delete_without_dry_run_requires_confirm() -> None:
    """A real bulk delete is a mutation and needs --confirm."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(),
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch("thytrader.research.http.bulk_delete_strategies") as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(["bulk-delete-strategies", "--strategy-id", _STRATEGY_ID])
    assert "Pass --confirm" in str(raised.value)
    request.assert_not_called()


def test_list_results_help_documents_cursor_and_max_100(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operators must see the page cap, cursor, and strategy filters before a 422."""
    with pytest.raises(SystemExit) as raised:
        main(["list-results", "--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out.lower()
    assert "maximum 100" in output or "max 100" in output
    assert "--cursor" in output
    assert "has_more" in output or "next page" in output
    assert "--strategy-id" in output


def test_list_strategies_help_documents_cursor(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Strategy library listing is cursor-paginated like result listing."""
    with pytest.raises(SystemExit) as raised:
        main(["list-strategies", "--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out.lower()
    assert "--cursor" in output
    assert "--limit" in output
    assert "maximum 100" in output or "max 100" in output


def test_list_strategies_reports_validity_and_current_fingerprint(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Each listed strategy shows whether it can start and which rules would run."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/strategies": _library_payload(),
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        pytest.raises(SystemExit) as raised,
    ):
        main(["list-strategies"])
    assert raised.value.code == EXIT_HEALTHY
    payload = json.loads(capsys.readouterr().out)
    assert [row["valid"] for row in payload["strategies"]] == [True, False]
    assert payload["strategies"][0]["current_fingerprint"] == "sha256:" + "c" * 64
    assert payload["strategies"][0]["active_deployment_count"] == 1
    assert payload["total"] == 2


def test_save_strategy_puts_the_document_with_its_revision(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Work-in-progress documents save in place; the CLI sends the edited revision."""
    payload = json.loads(_REFERENCE_STRATEGY.read_text())
    payload["entry"]["when"]["all"][0]["right"] = {"literal": "50"}
    path = tmp_path / "strategy.json"
    path.write_text(json.dumps(payload))
    sent: list[dict[str, object]] = []
    saved = {
        **_strategy_response(),
        "revision": 4,
        "validation": {"valid": False, "issues": [{"loc": "entry", "message": "bad"}]},
        "current_fingerprint": None,
    }

    def fake_mutation(
        *, method: str, url: str, payload: dict[str, object], **_kw: object
    ) -> object:
        assert method == "PUT"
        assert url.endswith(f"/api/v1/strategies/{_STRATEGY_ID}")
        sent.append(payload)
        return saved

    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(matching_ready_payload()),
        ),
        patch(
            "thytrader.research.http_strategies.request_mutation_json", side_effect=fake_mutation
        ),
        pytest.raises(SystemExit) as raised,
    ):
        main(
            [
                "save-strategy",
                "--strategy-id",
                _STRATEGY_ID,
                "--file",
                str(path),
                "--revision",
                "3",
                "--confirm",
            ]
        )
    assert raised.value.code == EXIT_HEALTHY
    assert sent[0]["revision"] == 3
    assert sent[0]["document"] == payload
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["valid"] is False
    assert output["validation"] == {
        "valid": False,
        "issues": [{"loc": "entry", "message": "bad"}],
        "warnings": [],
    }
    assert output["revision"] == 4
    assert "saved as an INVALID draft (1 issue): entry: bad" in captured.err


def test_import_strategy_reports_validation_like_show_strategy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """import-strategy nests validation exactly as show-strategy does and flags invalid drafts."""
    imported = {
        **_strategy_response(),
        "validation": {
            "valid": False,
            "issues": [
                {"loc": "entry.when.all[0].left.input", "message": 'unknown field "input"'},
                {"loc": "entry.when.all[0].left", "message": "must be an indicator operand"},
            ],
            "warnings": [],
        },
        "current_fingerprint": None,
    }

    def fake_mutation(*, method: str, url: str, **_kw: object) -> object:
        assert method == "POST"
        assert url.endswith("/api/v1/strategies/import")
        return imported

    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(matching_ready_payload()),
        ),
        patch(
            "thytrader.research.http_strategies.request_mutation_json", side_effect=fake_mutation
        ),
        pytest.raises(SystemExit) as raised,
    ):
        main(["import-strategy", "--file", str(_REFERENCE_STRATEGY), "--confirm"])
    assert raised.value.code == EXIT_HEALTHY
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["validation"] == imported["validation"]
    assert output["valid"] is False
    assert captured.err.startswith(
        "thytrader-research: imported as an INVALID draft (2 issues): "
        'entry.when.all[0].left.input: unknown field "input".'
    )


def test_valid_import_prints_no_invalid_notice(capsys: pytest.CaptureFixture[str]) -> None:
    """A valid result keeps stderr quiet."""
    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(matching_ready_payload()),
        ),
        patch(
            "thytrader.research.http_strategies.request_mutation_json",
            return_value=_strategy_response(),
        ),
        pytest.raises(SystemExit) as raised,
    ):
        main(["import-strategy", "--file", str(_REFERENCE_STRATEGY), "--confirm"])
    assert raised.value.code == EXIT_HEALTHY
    captured = capsys.readouterr()
    assert json.loads(captured.out)["validation"]["valid"] is True
    assert captured.err == ""


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


def test_create_strategy_help_allows_five_minute_paper(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """create-draft help must name ingested venue clocks for paper and live."""
    with pytest.raises(SystemExit) as raised:
        main(["create-strategy", "--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out.lower()
    collapsed = " ".join(output.split())
    assert "any ingested venue clock" in collapsed
    assert "live stays 1h" not in collapsed
    assert "1h or 5m" not in collapsed


def test_create_strategy_without_confirm_does_not_write() -> None:
    """Omitting --confirm in Safe mode exits after the YOLO probe, before create-draft HTTP."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(),
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch("thytrader.research.http.create_strategy") as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(["create-strategy"])
    assert raised.value.code != 0
    assert "Pass --confirm" in str(raised.value)
    request.assert_not_called()


def test_save_strategy_help_documents_revision_guard(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """save-draft help must explain that new identities start at revision 1."""
    with pytest.raises(SystemExit) as raised:
        main(["save-strategy", "--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out.lower()
    assert "revision" in output
    assert "stale" in output


def test_import_strategy_without_confirm_does_not_write() -> None:
    """Omitting --confirm must exit before import-draft HTTP."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(),
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch("thytrader.research.http.import_strategy") as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(["import-strategy", "--file", str(_REFERENCE_STRATEGY)])
    assert raised.value.code != 0
    assert "Pass --confirm" in str(raised.value)
    request.assert_not_called()


def test_submit_backtest_stale_engine_422_hints_rebuild(tmp_path: Path) -> None:
    """A stale API that still demands an engine selector must tell operators to rebuild."""
    path = tmp_path / "request.json"
    path.write_text("{}")
    with (
        patch(
            "thytrader.research.mutation_cli.BacktestStartRequest.model_validate",
            return_value=object(),
        ),
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(matching_ready_payload()),
        ),
        patch(
            "thytrader.research.http.submit_backtest",
            side_effect=AgentHttpError("HTTP 422: engine_contract_version: Field required"),
        ),
        pytest.raises(SystemExit) as raised,
    ):
        main(["submit-backtest", "--file", str(path), "--confirm"])
    message = str(raised.value)
    assert "422" in message
    assert "make run" in message
    assert "failed safely" not in message


def test_submit_backtest_rejects_a_file_that_still_selects_an_engine(tmp_path: Path) -> None:
    """A start document naming engine_contract_version fails locally with the migration text."""
    path = tmp_path / "request.json"
    path.write_text(
        json.dumps(
            {
                "strategy_id": "11111111-1111-1111-1111-111111111111",
                "dataset_fingerprint": "sha256:" + "b" * 64,
                "initial_quote_balance": "10000",
                "maker_fee_rate": "0.001",
                "taker_fee_rate": "0.002",
                "fixed_slippage_bps": "10",
                "engine_contract_version": "thytrader-backtest",
            }
        )
    )
    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(matching_ready_payload()),
        ),
        patch("thytrader.research.http.submit_backtest") as submit,
        pytest.raises(SystemExit) as raised,
    ):
        main(["submit-backtest", "--file", str(path), "--confirm"])
    assert "engine_contract_version was removed" in str(raised.value)
    submit.assert_not_called()


def test_backtest_model_command_describes_the_single_model_locally() -> None:
    """The local backtest-model command prints the unified engine and its assumptions."""
    output = io.StringIO()
    with redirect_stdout(output), pytest.raises(SystemExit) as raised:
        main(["backtest-model", "--local"])
    assert raised.value.code == EXIT_HEALTHY
    document = json.loads(output.getvalue())
    assert document["engine"] == "thytrader-backtest"
    assert {item["key"] for item in document["assumptions"]} >= {"maker_entries", "spread_stress"}


def test_create_strategy_yolo_skips_confirm() -> None:
    """Research YOLO records a skip then creates a draft without --confirm."""
    skip = {
        "id": "11111111-1111-1111-1111-111111111111",
        "category": "research",
        "action": "confirm_skipped",
        "outcome": "info",
        "tier": "research",
        "command": "create-strategy",
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
            "thytrader.research.http.create_strategy",
            return_value='{"strategy_id":"x"}',
        ) as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(["create-strategy"])
    assert raised.value.code == 0
    request.assert_called_once()


def test_research_cli_refuses_stale_ops_contract_before_command() -> None:
    """Every HTTP research command stops when the ready API contract is stale."""
    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(stale_ready_payload()),
        ),
        patch("thytrader.research.http.create_strategy") as request,
        pytest.raises(SystemExit, match="make run"),
    ):
        main(["create-strategy", "--confirm"])
    request.assert_not_called()


def test_create_strategy_help_lists_research_templates(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operators can discover the Phase 11 template ids without a database."""
    with pytest.raises(SystemExit) as raised:
        main(["create-strategy", "--help"])
    assert raised.value.code == 0
    output = " ".join(capsys.readouterr().out.split())
    assert "--template" in output
    assert "macd-trend" in output
    assert "mean-reversion" in output
    assert "bollinger" in output
    # argparse may wrap the id at a hyphen (``ema-trend- hold`` once whitespace is joined).
    assert "ema-trend-hold" in output.replace("- ", "-")


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
    assert "ema-trend-hold" in ids


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


def test_create_strategy_local_refuses_experiential_model_id() -> None:
    """Gated advisory input is HTTP-only."""
    with pytest.raises(SystemExit, match="HTTP transport") as raised:
        main(
            [
                "--local",
                "create-strategy",
                "--experiential-model-id",
                str(_MODEL_ID),
                "--confirm",
            ]
        )
    assert raised.value.code != 0


def test_create_strategy_merges_experiential_advisory(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """HTTP create-draft JSON includes the fail-closed advisory, not a live policy."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        f"GET /api/v1/memory/models/{_MODEL_ID}": _trained_model_payload(),
        "POST /api/v1/strategies": _strategy_response(),
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        pytest.raises(SystemExit) as raised,
    ):
        main(
            [
                "create-strategy",
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


def test_create_strategy_missing_model_does_not_create() -> None:
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
                "create-strategy",
                "--experiential-model-id",
                str(_MODEL_ID),
                "--confirm",
            ]
        )
    assert raised.value.code != 0
    assert "failed safely" not in str(raised.value).lower()


def test_create_strategy_help_names_experiential_model(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operators can discover the gated advisory flag from --help."""
    with pytest.raises(SystemExit) as raised:
        main(["create-strategy", "--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out.lower()
    assert "--experiential-model-id" in output
    assert "http" in output


def test_submit_backtest_timeout_says_the_run_may_still_be_running(tmp_path: Path) -> None:
    """A synchronous submit that times out names the readback and --async instead of hiding."""
    path = tmp_path / "request.json"
    path.write_text("{}")
    timed_out = AgentHttpError(
        "Timed out after 30 s waiting for the ThyTrader API to answer POST /api/v1/backtests.",
        timed_out=True,
    )
    with (
        patch(
            "thytrader.research.mutation_cli.BacktestStartRequest.model_validate",
            return_value=object(),
        ),
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(matching_ready_payload()),
        ),
        patch("thytrader.research.http.submit_backtest", side_effect=timed_out),
        pytest.raises(SystemExit) as raised,
    ):
        main(["submit-backtest", "--file", str(path), "--confirm"])
    message = str(raised.value)
    assert "Timed out after 30 s" in message
    assert "may still be running" in message
    assert "list-results" in message
    assert "--async" in message
    assert "failed safely" not in message


def _study_file(tmp_path: Path) -> Path:
    """Write a minimal walk-forward-optimization study body to disk."""
    path = tmp_path / "wfo.json"
    path.write_text(
        json.dumps(
            {
                "kind": "walk_forward_optimization",
                "evaluation_start": "2022-01-01T00:00:00Z",
                "evaluation_end": "2026-10-01T00:00:00Z",
                "initial_quote_balance": "1000",
                "maker_fee_rate": "0.004",
                "taker_fee_rate": "0.006",
                "fixed_slippage_bps": "5",
                "strategy_id": _STRATEGY_ID,
                "in_sample_bars": 2000,
                "out_of_sample_bars": 500,
                "step_bars": 500,
                "parameter_axes": [
                    {"indicator_id": "fast", "parameter": "period", "values": ["8", "12"]}
                ],
            }
        )
    )
    return path


def test_plan_study_422_prints_the_api_code_and_message(tmp_path: Path) -> None:
    """A 4xx plan rejection surfaces detail.code and detail.message, not "failed safely"."""
    rejection = {
        "detail": {
            "code": "study_window_rejected",
            "message": (
                "evaluation_start 2022-01-01T00:00:00Z requires warmup coverage before the "
                "dataset starts. Suggested range: 2022-01-21T00:00:00Z to 2026-10-01T00:00:00Z."
            ),
        }
    }

    def fake_urlopen(request: object, timeout: object = None) -> object:
        del timeout
        url = str(getattr(request, "full_url", request))
        if url.endswith("/health/ready"):
            return json_urlopen_response(matching_ready_payload())
        assert url.endswith("/api/v1/research/studies/plan"), url
        return json_urlopen_response(rejection, status=422)

    with (
        patch("thytrader.agent_http.urlopen", side_effect=fake_urlopen),
        pytest.raises(SystemExit) as raised,
    ):
        main(["plan-study", "--file", str(_study_file(tmp_path))])
    message = str(raised.value)
    assert message.startswith("HTTP 422 study_window_rejected: evaluation_start")
    assert "Suggested range: 2022-01-21T00:00:00Z to 2026-10-01T00:00:00Z." in message
    assert "failed safely" not in message


def test_plan_study_missing_file_names_the_path(tmp_path: Path) -> None:
    """An unreadable --file says which path failed instead of a generic safe-failure line."""
    missing = tmp_path / "nope.json"
    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(matching_ready_payload()),
        ),
        pytest.raises(SystemExit) as raised,
    ):
        main(["plan-study", "--file", str(missing)])
    message = str(raised.value)
    assert f"could not read {missing}" in message
    assert "No such file or directory" in message
    assert "Paper and live state were not changed." in message
    assert "failed safely" not in message


def test_plan_study_invalid_json_names_the_position(tmp_path: Path) -> None:
    """A malformed JSON document reports the parser position."""
    path = tmp_path / "broken.json"
    path.write_text('{"kind": "oos_holdout",')
    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(matching_ready_payload()),
        ),
        pytest.raises(SystemExit) as raised,
    ):
        main(["plan-study", "--file", str(path)])
    message = str(raised.value)
    assert "input is not valid JSON" in message
    assert "line 1 column" in message


def test_show_backtest_job_dropped_connection_says_retry_the_read() -> None:
    """Under load the API may drop a read; the CLI says so instead of "failed safely"."""

    def fake_urlopen(request: object, timeout: object = None) -> object:
        del timeout
        url = str(getattr(request, "full_url", request))
        if url.endswith("/health/ready"):
            return json_urlopen_response(matching_ready_payload())
        raise ConnectionResetError(104, "Connection reset by peer")

    with (
        patch("thytrader.agent_http.urlopen", side_effect=fake_urlopen),
        pytest.raises(SystemExit) as raised,
    ):
        main(["show-backtest-job", "--job-id", "0199aaaa-aaaa-7aaa-aaaa-aaaaaaaaaaab"])
    message = str(raised.value)
    assert "closed the connection before answering GET /api/v1/research/jobs/" in message
    assert "retry the read" in message
    assert "failed safely" not in message
