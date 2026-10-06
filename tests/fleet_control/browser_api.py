"""Database/credential-free loopback API for the isolated fleet browser regressions.

Only generated test installation auth is used. No YAML/.env or venue credentials
are loaded and all execution persistence is disabled. Mutations in browser tests
are intercepted by Playwright, never sent to a live broker.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import SecretStr
import uvicorn

from thytrader.api.app import create_app
from thytrader.config import Environment, Settings


def main() -> None:
    """Start an exclusively test-owned API on a port unrelated to production."""
    settings = Settings(
        _env_file=None,
        environment=Environment.TEST,
        database_url=None,
        coinbase_api_key_name=None,
        coinbase_api_private_key=None,
        notify_webhook_url=None,
        yolo_enabled=False,
        yolo_tiers=(),
        installation_token=SecretStr(os.environ["CONTROLS_TEST_INSTALLATION_TOKEN"]),
        credentials_dir=Path(os.environ["CONTROLS_TEST_CREDENTIALS_DIR"]),
    )
    uvicorn.run(create_app(settings), host="127.0.0.1", port=28167, log_level="warning")


if __name__ == "__main__":
    main()
