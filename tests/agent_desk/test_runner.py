"""The desk runner: harness templates, a scrubbed run, the wake lock, and schedules."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

import pytest

from thytrader.agent_desk import runner
from thytrader.agent_desk.cli import main
from thytrader.agent_desk.harness import (
    BUILTIN_HARNESSES,
    HarnessError,
    custom_template,
    harness_argv,
    run_harness,
)

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


def _fake_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[dict[str, object]]:
    """Stand in for the briefing and wake routes; return the posted wakes."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    posted: list[dict[str, object]] = []
    brief = {
        "fingerprint": "sha256:" + "a" * 64,
        "prompt": "decide",
        "desk": {"harness": "claude", "model": None},
    }
    monkeypatch.setattr(runner, "desk_briefing", lambda _base, _desk: brief)

    def record(_base: str, _desk: str, payload: dict[str, object]) -> dict[str, object]:
        posted.append(payload)
        return {"outcome": "applied", **payload}

    monkeypatch.setattr(runner, "record_wake", record)
    return posted


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
    assert posted[0]["raw_response"] == "DECIDE\n"
    assert posted[0]["briefing_fingerprint"] == "sha256:" + "a" * 64
    assert posted[0]["harness"] == "custom"


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
    assert posted[0]["raw_response"] == ""
    assert str(posted[0]["harness_error"]).startswith("DESK_HARNESS_NOT_FOUND")


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


def test_an_unknown_harness_is_refused_before_anything_runs() -> None:
    """Only built-ins resolve by name; anything else needs --command."""
    with pytest.raises(runner.DeskRunError, match="DESK_HARNESS_UNKNOWN"):
        runner.resolve_template("opencode", None)


def test_schedules_wake_the_desk_on_its_cadence() -> None:
    """The systemd timer repeats at the cadence; cron needs a cadence that divides an hour."""
    timer = runner.schedule_text("0193abcd-desk", cadence_minutes=30, style="systemd")
    cron = runner.schedule_text("0193abcd-desk", cadence_minutes=30, style="cron")
    assert "OnUnitActiveSec=30min" in timer
    assert "wake 0193abcd-desk --confirm" in timer
    assert cron.startswith("*/30 * * * * cd ")
    with pytest.raises(runner.DeskRunError, match="DESK_SCHEDULE_UNSUPPORTED"):
        runner.schedule_text("d", cadence_minutes=45, style="cron")


def test_mutations_need_confirm(capsys: pytest.CaptureFixture[str]) -> None:
    """Create, wake, record and status refuse without --confirm before any HTTP call."""
    with pytest.raises(SystemExit, match="--confirm"):
        main(["wake", "desk-1"])
    with pytest.raises(SystemExit, match="--confirm"):
        main(["status", "desk-1", "paused"])
    main(["harnesses"])
    assert "claude" in json.loads(capsys.readouterr().out)
