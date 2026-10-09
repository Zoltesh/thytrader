"""``thytrader-runtime`` inventory adoption commands: parsing, gates and HTTP bodies."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest

from tests.http_fakes import json_urlopen_response, matching_ready_payload
from thytrader.runtime_control.cli import main

if TYPE_CHECKING:
    from unittest.mock import MagicMock

_ADOPT = [
    "place-order",
    "--mode",
    "live",
    "--entry-kind",
    "adopt",
    "--product-id",
    "DOGE-USDC",
    "--quantity",
    "all",
    "--stop-price",
    "0.15",
    "--take-profit-price",
    "0.3",
    "--idempotency-key",
    "adopt-doge-1",
]
_SELL = [
    "sell-holdings",
    "--product-id",
    "DOGE-USDC",
    "--quantity",
    "all",
    "--idempotency-key",
    "sell-doge-1",
]


class _Recorder:
    """A loopback API double that records every request and its JSON body."""

    def __init__(self) -> None:
        """Start with no requests."""
        self.requests: list[tuple[str, str, object]] = []

    def __call__(self, request: object, timeout: object = None) -> MagicMock:
        """Answer readiness and adoption routes; anything else fails the test."""
        del timeout
        method = str(getattr(request, "method", "GET")).upper()
        url = str(getattr(request, "full_url", request))
        raw = getattr(request, "data", None)
        body = None if raw is None else json.loads(raw)
        self.requests.append((method, url, body))
        path = urlparse(url).path
        if path == "/health/ready":
            return json_urlopen_response(matching_ready_payload())
        if path.startswith("/api/v1/inventory-adoptions") or path == "/api/v1/deployments":
            return json_urlopen_response({"id": "book", "status": "running"})
        raise AssertionError(f"unexpected agent HTTP request: {method} {path}")

    def posts(self, route: str = "/inventory-adoptions") -> list[object]:
        """Bodies of every POST to ``route``."""
        return [body for method, url, body in self.requests if method == "POST" and route in url]


def _run(argv: list[str]) -> tuple[_Recorder, int | str | None]:
    """Run the CLI against the recorder; return it and the exit code or message."""
    recorder = _Recorder()
    with (
        patch("thytrader.agent_http.urlopen", side_effect=recorder),
        pytest.raises(SystemExit) as raised,
    ):
        main(argv)
    return recorder, raised.value.code


def test_help_lists_adoption_commands_and_the_adopt_entry_kind(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An operator agent can discover preview, adopt and sell from --help alone."""
    with pytest.raises(SystemExit):
        main(["--help"])
    top = " ".join(capsys.readouterr().out.split())
    assert "adoption-preview" in top and "sell-holdings" in top
    with pytest.raises(SystemExit):
        main(["place-order", "--help"])
    place = " ".join(capsys.readouterr().out.split())
    assert "adopt" in place and "already held" in place


def test_place_order_adopt_posts_a_protect_adoption() -> None:
    """Confirmed and acknowledged, adopt posts action=protect with the levels and 'all'."""
    recorder, code = _run([*_ADOPT, "--confirm", "--i-understand-live"])
    assert code == 0
    assert recorder.posts() == [
        {
            "mode": "live",
            "action": "protect",
            "product_id": "DOGE-USDC",
            "quantity": "all",
            "idempotency_key": "adopt-doge-1",
            "origin": "agent",
            "timeframe": "5m",
            "i_understand_live": True,
            "stop_price": "0.15",
            "take_profit_price": "0.3",
        }
    ]


def test_sell_holdings_posts_a_sell_adoption() -> None:
    """sell-holdings posts action=sell without levels."""
    recorder, code = _run([*_SELL, "--note", "to USDC", "--confirm", "--i-understand-live"])
    assert code == 0
    assert recorder.posts() == [
        {
            "mode": "live",
            "action": "sell",
            "product_id": "DOGE-USDC",
            "quantity": "all",
            "idempotency_key": "sell-doge-1",
            "origin": "agent",
            "timeframe": "5m",
            "i_understand_live": True,
            "note": "to USDC",
        }
    ]


@pytest.mark.parametrize(
    "argv",
    [
        [*_ADOPT, "--confirm"],
        [*_SELL, "--confirm"],
        [*_ADOPT, "--i-understand-live"],
        [*_SELL, "--i-understand-live"],
    ],
)
def test_live_ack_and_confirm_are_both_required(argv: list[str]) -> None:
    """Neither flag alone sends anything; YOLO never covers these commands."""
    recorder, code = _run(argv)
    assert code not in (0, None)
    assert recorder.posts() == []


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (
            [*_ADOPT[:2], "paper", *_ADOPT[3:]],
            "ADOPTION_LIVE_ONLY",
        ),
        (
            [item for index, item in enumerate(_ADOPT) if index not in (7, 8)],
            "--quantity",
        ),
    ],
)
def test_adopt_refuses_paper_and_a_missing_quantity(argv: list[str], message: str) -> None:
    """Paper holds no venue coins; adopt needs N or all."""
    recorder, code = _run([*argv, "--confirm", "--i-understand-live"])
    assert isinstance(code, str) and message in code
    assert recorder.posts() == []


@pytest.mark.parametrize(
    "extra",
    [
        ["--quote-notional", "10"],
        ["--limit-price", "0.2"],
        ["--cash", "100"],
        ["--side", "short"],
    ],
)
def test_adopt_rejects_buy_only_flags(extra: list[str]) -> None:
    """Adoption buys nothing, so sizing, limit and paper flags are refused."""
    recorder, code = _run([*_ADOPT, *extra, "--confirm", "--i-understand-live"])
    assert isinstance(code, str) and code
    assert recorder.posts() == []


def test_adoption_preview_reads_without_mutating() -> None:
    """The preview is one GET with product and clock; no confirm is needed."""
    recorder, code = _run(["adoption-preview", "--product-id", "DOGE-USDC", "--timeframe", "1h"])
    assert code == 0
    [preview] = [url for method, url, _body in recorder.requests if "/inventory" in url]
    query = parse_qs(urlparse(preview).query)
    assert query == {"product_id": ["DOGE-USDC"], "timeframe": ["1h"]}
    assert recorder.posts() == []


_START = ["start", "--strategy-id", "11111111-1111-1111-1111-111111111111", "--mode", "live"]


def test_start_with_adopt_holdings_posts_it() -> None:
    """--adopt-holdings is forwarded on the live start body."""
    recorder, code = _run([*_START, "--adopt-holdings", "all", "--confirm", "--i-understand-live"])
    assert code == 0
    assert recorder.posts("/api/v1/deployments") == [
        {
            "strategy_id": "11111111-1111-1111-1111-111111111111",
            "mode": "live",
            "i_understand_live": True,
            "adopt_holdings": "all",
        }
    ]


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (
            [
                "start",
                "--strategy-id",
                "11111111-1111-1111-1111-111111111111",
                "--mode",
                "paper",
                "--cash",
                "100",
                "--adopt-holdings",
                "all",
                "--confirm",
            ],
            "ADOPTION_LIVE_ONLY",
        ),
        ([*_START, "--adopt-holdings", "all", "--i-understand-live"], "--confirm"),
    ],
)
def test_start_with_adoption_is_live_only_and_always_confirmed(
    argv: list[str], message: str
) -> None:
    """Paper is refused locally; without --confirm nothing is sent, whatever YOLO says."""
    recorder, code = _run(argv)
    assert isinstance(code, str) and message in code
    assert recorder.posts("/api/v1/deployments") == []
