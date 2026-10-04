"""Read-only economic POSTs retain the installation authentication boundary."""

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from pydantic import SecretStr

from thytrader.api.app import create_app
from thytrader.config import Environment, Settings
from thytrader.research.http import economics

if TYPE_CHECKING:
    from urllib.request import Request


def test_economic_client_authenticates_against_the_real_api() -> None:
    """An unauthenticated calculation is denied; the CLI client authenticates without a write."""
    settings = Settings(
        environment=Environment.TEST,
        installation_token=SecretStr("isolated-economic-test-token"),
        trust_boundary_enabled=True,
    )
    payload = {
        "entry_price": "100",
        "stop_price": "98",
        "target_price": "100.5",
        "maker_fee_rate": "0.005",
        "taker_fee_rate": "0.009",
    }
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8200") as client:
        assert client.post("/api/v1/research/economics", json=payload).status_code == 401

        def forward(request: Request, timeout: float = 30) -> MagicMock:
            """Route the actual authenticated client request through the real ASGI app."""
            del timeout
            assert request.get_method() == "POST"
            assert isinstance(request.data, bytes)
            response = client.post(
                "/api/v1/research/economics",
                content=request.data,
                headers=dict(request.header_items()),
            )
            assert response.status_code == 200
            opened = MagicMock()
            opened.status = response.status_code
            opened.read.return_value = response.content
            opened.__enter__.return_value = opened
            opened.__exit__.return_value = None
            return opened

        with (
            patch("thytrader.agent_http.Settings", return_value=settings),
            patch("thytrader.agent_http.urlopen", side_effect=forward),
        ):
            result = json.loads(economics("http://127.0.0.1:8200", payload))
        assert result["net_target_quote_pnl"] == "-0.5025"
        assert result["target_clears_costs"] is False
