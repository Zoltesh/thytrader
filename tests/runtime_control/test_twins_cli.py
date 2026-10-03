"""Twin metadata commands are discoverable and never inherit YOLO/live authority."""

import json
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest

from tests.http_fakes import matching_ready_payload, urlopen_ready_then
from thytrader.runtime_control.cli import main

_PAPER = "01985cf0-7b60-7000-8000-00000000aa01"
_LIVE = "01985cf0-7b60-7000-8000-00000000aa02"


@pytest.mark.parametrize("command", ["link-twin", "unlink-twin"])
def test_twin_metadata_always_requires_confirm_before_http(command: str) -> None:
    """Neither an orchestration probe nor a mutation happens without explicit approval."""
    with patch("thytrader.agent_http.urlopen") as request, pytest.raises(SystemExit) as raised:
        main([command, _PAPER, "--counterpart-deployment-id", _LIVE])
    assert "--confirm" in str(raised.value)
    request.assert_not_called()


@pytest.mark.parametrize("command,method", [("link-twin", "PUT"), ("unlink-twin", "DELETE")])
def test_confirmed_metadata_does_not_require_a_live_acknowledgement(
    command: str, method: str
) -> None:
    """Only the intended pair is sent; no start/resume/order endpoint is available here."""
    with patch(
        "thytrader.agent_http.urlopen",
        side_effect=urlopen_ready_then(
            matching_ready_payload(), {"deployment_id": _PAPER, "twin": None}
        ),
    ) as request:
        with pytest.raises(SystemExit) as raised:
            main([command, _PAPER, "--counterpart-deployment-id", _LIVE, "--confirm"])
        assert raised.value.code == 0
    mutation = request.call_args_list[-1].args[0]
    assert mutation.get_method() == method
    assert urlsplit(mutation.full_url).path == f"/api/v1/deployments/{_PAPER}/twin"
    if method == "PUT":
        assert json.loads(mutation.data) == {"counterpart_deployment_id": _LIVE}
    else:
        assert f"counterpart_deployment_id={_LIVE}" in mutation.full_url


def test_show_twin_is_read_only() -> None:
    """Agents can discover the pair with no confirmation or mutation."""
    with patch(
        "thytrader.agent_http.urlopen",
        side_effect=urlopen_ready_then(
            matching_ready_payload(), {"deployment_id": _PAPER, "twin": None}
        ),
    ) as request:
        with pytest.raises(SystemExit) as raised:
            main(["show-twin", _PAPER])
        assert raised.value.code == 0
    assert request.call_args_list[-1].args[0].get_method() == "GET"
