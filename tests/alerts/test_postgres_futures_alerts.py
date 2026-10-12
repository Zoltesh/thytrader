"""Futures alerts retain identities and recovery watermarks across PostgreSQL readers."""

from datetime import timedelta
from typing import TYPE_CHECKING

from pydantic import SecretStr
import pytest

from tests.alerts.test_postgres_alerts import (
    _NOW,
    _URL,
    private_target,
    scratch_database,
    store,
)
from thytrader.alerts.models import (
    AlertCheck,
    AlertCode,
    AlertScope,
    AlertSeverity,
    SupervisionFinding,
)
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_alerts import PostgresAlertStore

if TYPE_CHECKING:
    from thytrader.alerts.store import AlertStore

__all__ = ["private_target", "scratch_database", "store"]
pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(_URL is None, reason="Private test DB required."),
]


@pytest.mark.parametrize("code", [code for code in AlertCode if code.value.startswith("FUTURES_")])
async def test_futures_alert_restart_dedup_and_recovery(
    store: AlertStore,
    scratch_database: str,
    code: AlertCode,
) -> None:
    """Each futures code round-trips; unknown and replay cannot resolve or duplicate it."""
    finding = SupervisionFinding(
        code=code,
        scope=AlertScope.FLEET,
        subject="futures:live",
        severity=AlertSeverity.CRITICAL,
        detail="Synthetic futures observation.",
    )
    await store.apply_observations((finding,), (), now=_NOW, detail="")
    engine = create_engine(SecretStr(scratch_database))
    try:
        restarted = PostgresAlertStore(engine)
        await restarted.apply_observations(
            (finding,), (), now=_NOW + timedelta(seconds=1), detail=""
        )
        await restarted.apply_observations((), (), now=_NOW + timedelta(seconds=2), detail="")
        rows = await restarted.list_open_alerts()
        assert len(rows) == 1
        assert rows[0].code is code
        assert rows[0].occurrences == 2
        check = AlertCheck(code, "futures:live")
        await restarted.apply_observations(
            (), (check,), now=_NOW + timedelta(seconds=3), detail="fresh"
        )
        await store.apply_observations((finding,), (), now=_NOW, detail="stale replay")
        assert not await restarted.list_open_alerts()
    finally:
        await dispose(engine)
