"""The desk runner: harness templates, a scrubbed run, the wake lock, and schedules."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

import pytest

from thytrader.agent_desk import cli, runner
from thytrader.agent_desk.cli import main
from thytrader.agent_desk.harness import (
    BUILTIN_HARNESSES,
    HarnessError,
    custom_template,
    harness_argv,
    run_harness,
)
from thytrader.agent_http import AgentHttpError

if TYPE_CHECKING:
    from pathlib import Path

_ECHO = f'{sys.executable} -c "import sys; print(sys.stdin.read().upper())"'
_ENV = f'{sys.executable} -c "import os, json; print(json.dumps(sorted(os.environ)))"'


def test_builtin_templates_put_the_model_where_each_harness_reads_it() -> None:
    """Claude, Pi and Codex take the prompt on stdin; Hermes takes it as its -z argument."""
    claude = harness_argv(BUILTIN_HARNESSES["claude"], model="m1")
    hermes = harness_argv(BUILTIN_HARNESSES["hermes"], model="m2")
    assert claude[:2] == ("claude", "-p")
    assert claude[claude.index("--tools") + 1] == ""
    assert claude[-2:] == ("--model", "m1")
    assert hermes == ("hermes", "-m", "m2", "--ignore-rules", "-z", "{prompt}")
    assert "--no-tools" in harness_argv(BUILTIN_HARNESSES["pi"], model=None)


def test_a_harness_reads_the_prompt_and_its_stdout_is_the_answer() -> None:
    """The prompt goes in on stdin; stdout comes back."""
    answer = run_harness(custom_template(_ECHO), prompt="hi", model=None, timeout_seconds=30)
    assert answer.strip() == "HI"


def test_the_harness_environment_has_no_thytrader_or_venue_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THYTRADER_* and COINBASE_* never reach the harness."""
    monkeypatch.setenv("THYTRADER_INSTALLATION_TOKEN", "secret")
    monkeypatch.setenv("COINBASE_API_KEY_NAME", "key")
    monkeypatch.setenv("DESK_TEST_VISIBLE", "1")
    names = json.loads(
        run_harness(custom_template(_ENV), prompt="", model=None, timeout_seconds=30)
    )
    assert "DESK_TEST_VISIBLE" in names
    assert not [name for name in names if name.startswith(("THYTRADER_", "COINBASE_"))]


@pytest.mark.parametrize(
    ("command", "code"),
    [
        ("definitely-not-a-harness-binary", "DESK_HARNESS_NOT_FOUND"),
        (f'{sys.executable} -c "import sys; sys.exit(3)"', "DESK_HARNESS_EXIT"),
        (f'{sys.executable} -c "pass"', "DESK_HARNESS_EMPTY"),
        (f'{sys.executable} -c "import time; time.sleep(5)"', "DESK_HARNESS_TIMEOUT"),
    ],
)
def test_harness_failures_carry_a_code(command: str, code: str) -> None:
    """Missing binaries, non-zero exits, silence and timeouts are distinct failures."""
    with pytest.raises(HarnessError, match=code):
        run_harness(custom_template(command), prompt="x", model=None, timeout_seconds=1)


def _fake_api(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, skip: bool = False
) -> list[dict[str, object]]:
    """Stand in for the briefing, skip and wake routes; return the posted wakes and skips."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    posted: list[dict[str, object]] = []
    brief = {
        "fingerprint": "sha256:" + "a" * 64,
        "material_fingerprint": "sha256:" + "b" * 64,
        "config_fingerprint": "sha256:" + "f" * 64,
        "harness": "claude",
        "model": "configured-model",
        "prompt": "decide",
        "desk": {"equity_quote": "10000"},
    }

    def skip_wake(_base: str, _desk: str, payload: dict[str, object]) -> dict[str, object]:
        posted.append({"skip_request": True, **payload})
        if skip:
            return {"skipped": True, "wake": {"outcome": "skipped", **payload}}
        return {"skipped": False, "reason": "DESK_WAKE_CHANGED: new bar."}

    monkeypatch.setattr(runner, "skip_wake", skip_wake)
    monkeypatch.setattr(runner, "desk_briefing", lambda _base, _desk: brief)
    monkeypatch.setattr(
        runner,
        "show_desk",
        lambda _base, _desk: {"desk": {"harness": "claude", "model": "configured-model"}},
    )

    def record(_base: str, _desk: str, payload: dict[str, object]) -> dict[str, object]:
        posted.append(payload)
        return {"outcome": "applied", **payload}

    monkeypatch.setattr(runner, "record_wake", record)
    return posted


def _answers(posted: list[dict[str, object]]) -> list[dict[str, object]]:
    """The posted answers, without the skip requests before them."""
    return [payload for payload in posted if "skip_request" not in payload]


def test_a_wake_posts_the_answer_against_the_briefing_fingerprint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The answer and the fingerprint the harness saw are what ThyTrader receives."""
    posted = _fake_api(monkeypatch, tmp_path)
    result = runner.wake(
        "http://127.0.0.1:8000",
        "desk-1",
        harness=None,
        model=None,
        command=_ECHO,
        timeout_seconds=30,
    )
    assert result["outcome"] == "applied"
    answers = _answers(posted)
    assert answers[0]["raw_response"] == "DECIDE\n"
    assert answers[0]["briefing_fingerprint"] == "sha256:" + "a" * 64
    assert answers[0]["material_fingerprint"] == "sha256:" + "b" * 64
    assert answers[0]["harness"] == "custom"


def test_a_failed_harness_is_still_journaled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A crash posts a harness_error so the journal shows the missed wake."""
    posted = _fake_api(monkeypatch, tmp_path)
    runner.wake(
        "http://127.0.0.1:8000",
        "desk-1",
        harness=None,
        model=None,
        command="definitely-not-a-harness-binary",
        timeout_seconds=30,
    )
    answers = _answers(posted)
    assert answers[0]["raw_response"] == ""
    assert str(answers[0]["harness_error"]).startswith("DESK_HARNESS_NOT_FOUND")


def test_an_overlapping_wake_is_skipped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """While one wake holds the desk lock, the next one returns a skip and posts nothing."""
    posted = _fake_api(monkeypatch, tmp_path)
    with runner._desk_lock("desk-1") as held:
        assert held
        skipped = runner.wake(
            "http://127.0.0.1:8000",
            "desk-1",
            harness=None,
            model=None,
            command=_ECHO,
            timeout_seconds=30,
        )
    assert skipped["skipped"] is True
    assert posted == []


def test_a_plain_wake_runs_the_desks_own_harness_and_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`wake DESK_ID --confirm`, the scheduled command, uses the harness and model on the desk."""
    posted = _fake_api(monkeypatch, tmp_path)
    seen: list[tuple[str, str | None]] = []

    def fake_run(
        template: object, *, prompt: str, model: str | None, timeout_seconds: float
    ) -> str:
        del prompt, timeout_seconds
        seen.append((getattr(template, "name", ""), model))
        return '{"action": "no_trade", "thesis": "x"}'

    monkeypatch.setattr(runner, "run_harness", fake_run)
    runner.wake(
        "http://127.0.0.1:8000",
        "desk-1",
        harness=None,
        model=None,
        command=None,
        timeout_seconds=30,
    )
    assert seen == [("claude", "configured-model")]
    answer = _answers(posted)[0]
    assert (answer["harness"], answer["model"]) == ("claude", "configured-model")
    assert (posted[0]["harness"], posted[0]["model"]) == ("claude", "configured-model")


def test_an_unchanged_wake_is_skipped_without_running_the_harness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When ThyTrader journals a skip, no harness runs and no briefing or answer is posted."""
    posted = _fake_api(monkeypatch, tmp_path, skip=True)
    ran: list[str] = []
    monkeypatch.setattr(runner, "run_harness", lambda *_a, **_k: ran.append("ran") or "")
    monkeypatch.setattr(
        runner, "desk_briefing", lambda *_a: pytest.fail("a skipped wake reads no briefing")
    )
    result = runner.wake(
        "http://127.0.0.1:8000",
        "desk-1",
        harness=None,
        model=None,
        command=None,
        timeout_seconds=30,
    )
    assert result["outcome"] == "skipped"
    assert ran == []
    assert _answers(posted) == []


def test_always_runs_the_harness_without_asking_to_skip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`wake --always` never asks for a skip, so the model is asked every time."""
    posted = _fake_api(monkeypatch, tmp_path, skip=True)
    result = runner.wake(
        "http://127.0.0.1:8000",
        "desk-1",
        harness=None,
        model=None,
        command=_ECHO,
        timeout_seconds=30,
        always=True,
    )
    assert result["outcome"] == "applied"
    assert [payload for payload in posted if "skip_request" in payload] == []
    assert _answers(posted)[0]["raw_response"] == "DECIDE\n"


def test_an_unknown_harness_is_refused_before_anything_runs() -> None:
    """Only built-ins resolve by name; anything else needs --command."""
    with pytest.raises(runner.DeskRunError, match="DESK_HARNESS_UNKNOWN"):
        runner.resolve_template("opencode", None)


def test_schedules_wake_the_desk_on_its_cadence() -> None:
    """The systemd timer repeats at the cadence; cron needs a cadence that divides an hour."""
    timer = runner.schedule_text("0193abcd-desk", cadence_minutes=30, style="systemd")
    other = runner.schedule_text("0193abcd-desk-two", cadence_minutes=30, style="systemd")
    assert "thytrader-desk-0193abcd-desk.timer" in timer
    assert "thytrader-desk-0193abcd-desk-two.timer" in other
    cron = runner.schedule_text("0193abcd-desk", cadence_minutes=30, style="cron")
    assert "OnUnitActiveSec=30min" in timer
    assert "wake 0193abcd-desk --confirm" in timer
    assert cron.startswith("*/30 * * * * cd ")
    custom = runner.schedule_text(
        "d",
        cadence_minutes=30,
        style="cron",
        base_url="http://127.0.0.1:9876",
        harness_command="opencode run --model {model}",
    )
    assert "--base-url http://127.0.0.1:9876" in custom
    assert "--command 'opencode run --model {model}'" in custom
    percent = 'printf %s \'{"action": "no_trade"}\''
    cron_line = runner.schedule_text("d", cadence_minutes=30, style="cron", harness_command=percent)
    unit = runner.schedule_text("d", cadence_minutes=30, style="systemd", harness_command=percent)
    assert "printf \\%s" in cron_line
    assert "printf %%s" in unit
    with pytest.raises(runner.DeskRunError, match="DESK_SCHEDULE_UNSUPPORTED"):
        runner.schedule_text("d", cadence_minutes=45, style="cron")


def test_mutations_need_confirm(capsys: pytest.CaptureFixture[str]) -> None:
    """Create, update, wake, record and status refuse without --confirm before any HTTP call."""
    with pytest.raises(SystemExit, match="--confirm"):
        main(["wake", "desk-1"])
    with pytest.raises(SystemExit, match="--confirm"):
        main(["status", "desk-1", "paused"])
    with pytest.raises(SystemExit, match="--confirm"):
        main(["update", "desk-1", "--model", "m"])
    main(["harnesses"])
    assert "claude" in json.loads(capsys.readouterr().out)


def test_an_unknown_desk_harness_is_journaled_as_a_failed_wake(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A harness the runner cannot resolve posts an error wake, so the failure alert sees it."""
    posted = _fake_api(monkeypatch, tmp_path)
    monkeypatch.setattr(
        runner, "show_desk", lambda _base, _desk: {"desk": {"harness": "gpt", "model": "m"}}
    )
    monkeypatch.setattr(
        runner,
        "desk_briefing",
        lambda _base, _desk: {
            "fingerprint": "sha256:" + "a" * 64,
            "config_fingerprint": "sha256:" + "f" * 64,
            "harness": "gpt",
            "model": "m",
            "prompt": "decide",
        },
    )
    monkeypatch.setattr(runner, "run_harness", lambda *_a, **_k: pytest.fail("no harness runs"))
    result = runner.wake(
        "http://127.0.0.1:8000",
        "desk-1",
        harness=None,
        model=None,
        command=None,
        timeout_seconds=30,
    )
    assert result["outcome"] == "applied"
    assert [payload for payload in posted if "skip_request" in payload] == []
    answer = _answers(posted)[0]
    assert str(answer["harness_error"]).startswith("DESK_HARNESS_UNKNOWN")
    assert answer["runner_fingerprint"] is None


def test_the_runner_identity_covers_the_command_template_and_model() -> None:
    """Two custom commands or two models are two runners; the same one is stable."""
    one = runner.runner_fingerprint(custom_template("tool-a run"), "m")
    assert one == runner.runner_fingerprint(custom_template("tool-a run"), "m")
    assert one != runner.runner_fingerprint(custom_template("tool-b run"), "m")
    assert one != runner.runner_fingerprint(custom_template("tool-a run"), "n")
    assert runner.runner_fingerprint(None, "m") is None


def test_a_wake_posts_the_briefings_configuration_and_its_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The answer carries the configuration fingerprint it was produced under."""
    posted = _fake_api(monkeypatch, tmp_path)
    brief = {
        "fingerprint": "sha256:" + "a" * 64,
        "material_fingerprint": "sha256:" + "b" * 64,
        "config_fingerprint": "sha256:" + "e" * 64,
        "harness": "claude",
        "model": "configured-model",
        "prompt": "decide",
    }
    monkeypatch.setattr(runner, "desk_briefing", lambda _base, _desk: brief)
    runner.wake(
        "http://127.0.0.1:8000",
        "desk-1",
        harness=None,
        model=None,
        command=_ECHO,
        timeout_seconds=30,
    )
    answer = _answers(posted)[0]
    skip = posted[0]
    assert answer["config_fingerprint"] == "sha256:" + "e" * 64
    assert answer["runner_fingerprint"] == skip["runner_fingerprint"]
    assert str(answer["runner_fingerprint"]).startswith("sha256:")


def _no_real_harness(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str | None]]:
    """Record which harness and model would run, without running anything."""
    seen: list[tuple[str, str | None]] = []

    def fake_run(
        template: object, *, prompt: str, model: str | None, timeout_seconds: float
    ) -> str:
        del prompt, timeout_seconds
        seen.append((getattr(template, "name", ""), model))
        return '{"action": "no_trade", "thesis": "x"}'

    monkeypatch.setattr(runner, "run_harness", fake_run)
    return seen


def test_a_model_switched_before_the_briefing_runs_the_briefings_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The desk read says model A, the briefing (after a PATCH) says B: B answers."""
    posted = _fake_api(monkeypatch, tmp_path)
    seen = _no_real_harness(monkeypatch)
    switched = {
        "fingerprint": "sha256:" + "a" * 64,
        "config_fingerprint": "sha256:" + "9" * 64,
        "harness": "pi",
        "model": "new-model",
        "prompt": "decide",
    }
    monkeypatch.setattr(runner, "desk_briefing", lambda _base, _desk: switched)
    runner.wake(
        "http://127.0.0.1:8000",
        "desk-1",
        harness=None,
        model=None,
        command=None,
        timeout_seconds=30,
    )
    answer = _answers(posted)[0]
    assert seen == [("pi", "new-model")]
    assert (answer["harness"], answer["model"]) == ("pi", "new-model")
    assert answer["config_fingerprint"] == "sha256:" + "9" * 64


def test_a_live_desk_takes_no_harness_or_model_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Overriding who trades real money needs `update --i-understand-live`, not a wake flag."""
    posted = _fake_api(monkeypatch, tmp_path)
    monkeypatch.setattr(
        runner,
        "show_desk",
        lambda _base, _desk: {"desk": {"mode": "live", "harness": "claude", "model": "a"}},
    )
    overrides: list[tuple[str | None, str | None]] = [
        (None, "b"),
        ("pi", None),
        (None, "a"),
        ("claude", None),
    ]
    monkeypatch.setattr(runner, "run_harness", lambda *_a, **_k: pytest.fail("no harness runs"))
    for harness, model in overrides:
        runner.wake(
            "http://127.0.0.1:8000",
            "desk-1",
            harness=harness,
            model=model,
            command=None,
            timeout_seconds=30,
        )
    answers = _answers(posted)
    assert len(answers) == len(overrides)
    assert all(str(a["harness_error"]).startswith("DESK_LIVE_OVERRIDE_REFUSED") for a in answers)
    assert [payload for payload in posted if "skip_request" in payload] == []


def test_a_live_built_in_harness_takes_no_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A live desk configured for a built-in harness really runs that harness."""
    posted = _fake_api(monkeypatch, tmp_path)
    monkeypatch.setattr(
        runner,
        "show_desk",
        lambda _base, _desk: {"desk": {"mode": "live", "harness": "claude", "model": "a"}},
    )
    monkeypatch.setattr(runner, "run_harness", lambda *_a, **_k: pytest.fail("no harness runs"))
    runner.wake(
        "http://127.0.0.1:8000",
        "desk-1",
        harness=None,
        model=None,
        command=_ECHO,
        timeout_seconds=30,
    )
    answer = _answers(posted)[0]
    assert str(answer["harness_error"]).startswith("DESK_LIVE_OVERRIDE_REFUSED")


def test_a_live_custom_command_answers_under_the_desks_own_harness_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--command is how a custom harness runs; a live answer carries the desk's identity."""
    posted = _fake_api(monkeypatch, tmp_path)
    monkeypatch.setattr(
        runner,
        "show_desk",
        lambda _base, _desk: {"desk": {"mode": "live", "harness": "opencode", "model": "a"}},
    )
    custom = {
        "fingerprint": "sha256:" + "a" * 64,
        "config_fingerprint": "sha256:" + "f" * 64,
        "harness": "opencode",
        "model": "a",
        "prompt": "decide",
    }
    monkeypatch.setattr(runner, "desk_briefing", lambda _base, _desk: custom)
    runner.wake(
        "http://127.0.0.1:8000",
        "desk-1",
        harness=None,
        model=None,
        command=_ECHO,
        timeout_seconds=30,
    )
    answer = _answers(posted)[0]
    assert (answer["harness"], answer["model"]) == ("opencode", "a")


@pytest.mark.parametrize(
    ("command", "code"),
    [
        ("   ", "DESK_HARNESS_INVALID"),
        ('tool "unbalanced', "DESK_HARNESS_FAILED: ValueError"),
    ],
)
def test_a_command_that_cannot_be_set_up_is_journaled_as_a_failed_wake(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, command: str, code: str
) -> None:
    """A blank or misquoted --command posts an error wake; nothing runs."""
    posted = _fake_api(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "run_harness", lambda *_a, **_k: pytest.fail("no harness runs"))
    runner.wake(
        "http://127.0.0.1:8000",
        "desk-1",
        harness=None,
        model=None,
        command=command,
        timeout_seconds=30,
    )
    answer = _answers(posted)[0]
    assert str(answer["harness_error"]).startswith(code)


def test_a_harness_that_cannot_start_is_journaled_as_a_failed_wake(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An OS-level failure (for example a binary without execute permission) posts an error."""
    posted = _fake_api(monkeypatch, tmp_path)

    def denied(*_args: object, **_kwargs: object) -> str:
        raise PermissionError("not executable")

    monkeypatch.setattr(runner, "run_harness", denied)
    runner.wake(
        "http://127.0.0.1:8000",
        "desk-1",
        harness=None,
        model=None,
        command=None,
        timeout_seconds=30,
    )
    answer = _answers(posted)[0]
    assert str(answer["harness_error"]).startswith("DESK_HARNESS_FAILED: PermissionError")


def test_the_runner_answers_the_briefing_the_skip_check_built(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A declined skip hands back its locked briefing; the runner answers it, not a re-read."""
    posted = _fake_api(monkeypatch, tmp_path)
    seen = _no_real_harness(monkeypatch)
    locked = {
        "fingerprint": "sha256:" + "1" * 64,
        "material_fingerprint": "sha256:" + "2" * 64,
        "config_fingerprint": "sha256:" + "3" * 64,
        "harness": "pi",
        "model": "locked-model",
        "prompt": "decide",
    }

    def skip_wake(_base: str, _desk: str, payload: dict[str, object]) -> dict[str, object]:
        posted.append({"skip_request": True, **payload})
        return {"skipped": False, "reason": "DESK_WAKE_CHANGED: x", "briefing": locked}

    monkeypatch.setattr(runner, "skip_wake", skip_wake)
    monkeypatch.setattr(
        runner, "desk_briefing", lambda *_a: pytest.fail("the locked briefing is answered")
    )
    runner.wake(
        "http://127.0.0.1:8000",
        "desk-1",
        harness=None,
        model=None,
        command=None,
        timeout_seconds=30,
    )
    answer = _answers(posted)[0]
    assert seen == [("pi", "locked-model")]
    assert answer["briefing_fingerprint"] == "sha256:" + "1" * 64
    assert answer["config_fingerprint"] == "sha256:" + "3" * 64
    assert (answer["harness"], answer["model"]) == ("pi", "locked-model")


def test_an_unanswered_skip_request_still_asks_the_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Skipping only saves a call: a failing skip endpoint never loses the wake."""
    posted = _fake_api(monkeypatch, tmp_path)

    def broken(_base: str, _desk: str, _payload: dict[str, object]) -> dict[str, object]:
        raise AgentHttpError("HTTP 503: desk store unavailable", status=503)

    monkeypatch.setattr(runner, "skip_wake", broken)
    runner.wake(
        "http://127.0.0.1:8000",
        "desk-1",
        harness=None,
        model=None,
        command=_ECHO,
        timeout_seconds=30,
    )
    assert _answers(posted)[0]["raw_response"] == "DECIDE\n"


def test_a_live_desk_switched_to_a_built_in_harness_mid_wake_refuses_the_old_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Desk read says custom opencode; the briefing (after an update) says built-in claude."""
    posted = _fake_api(monkeypatch, tmp_path)
    monkeypatch.setattr(
        runner,
        "show_desk",
        lambda _base, _desk: {"desk": {"mode": "live", "harness": "opencode", "model": "a"}},
    )
    switched = {
        "fingerprint": "sha256:" + "a" * 64,
        "config_fingerprint": "sha256:" + "9" * 64,
        "harness": "claude",
        "model": "new-model",
        "prompt": "decide",
    }

    def skip_wake(_base: str, _desk: str, payload: dict[str, object]) -> dict[str, object]:
        posted.append({"skip_request": True, **payload})
        return {"skipped": False, "reason": "DESK_WAKE_CHANGED: x", "briefing": switched}

    monkeypatch.setattr(runner, "skip_wake", skip_wake)
    monkeypatch.setattr(runner, "run_harness", lambda *_a, **_k: pytest.fail("no harness runs"))
    runner.wake(
        "http://127.0.0.1:8000",
        "desk-1",
        harness=None,
        model=None,
        command=_ECHO,
        timeout_seconds=30,
    )
    answer = _answers(posted)[0]
    assert str(answer["harness_error"]).startswith("DESK_LIVE_OVERRIDE_REFUSED")
    assert answer["harness"] == "claude"


def test_schedule_refuses_a_command_for_a_live_built_in_desk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI never prints a timer the runner would refuse."""
    monkeypatch.setattr(cli, "require_matching_ops_contract", lambda _base: None)
    monkeypatch.setattr(
        cli,
        "show_desk",
        lambda _base, _desk: {
            "desk": {"mode": "live", "harness": "claude", "cadence_minutes": 240}
        },
    )
    with pytest.raises(SystemExit, match="DESK_LIVE_OVERRIDE_REFUSED"):
        main(["schedule", "desk-1", "--command", "wrapper run"])


def test_a_live_desk_switched_to_a_custom_harness_mid_wake_runs_the_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Desk read says built-in claude, the briefing (after an update) says custom: it runs."""
    posted = _fake_api(monkeypatch, tmp_path)
    monkeypatch.setattr(
        runner,
        "show_desk",
        lambda _base, _desk: {"desk": {"mode": "live", "harness": "claude", "model": "a"}},
    )
    custom = {
        "fingerprint": "sha256:" + "a" * 64,
        "config_fingerprint": "sha256:" + "8" * 64,
        "harness": "opencode",
        "model": "a",
        "prompt": "decide",
    }
    monkeypatch.setattr(runner, "desk_briefing", lambda _base, _desk: custom)
    runner.wake(
        "http://127.0.0.1:8000",
        "desk-1",
        harness=None,
        model=None,
        command=_ECHO,
        timeout_seconds=30,
    )
    answer = _answers(posted)[0]
    assert "harness_error" not in answer
    assert answer["raw_response"] == "DECIDE\n"
    assert answer["harness"] == "opencode"
