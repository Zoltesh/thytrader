"""Stable futures safety codes are readable by the durable alert feed."""

from thytrader.alerts.models import AlertCode


def test_futures_alert_codes_are_registered() -> None:
    """All six visibility conditions use durable codes, not ad-hoc log messages."""
    expected = {
        "FUTURES_POSITION_MISMATCH",
        "FUTURES_ACCOUNT_UNKNOWN",
        "FUTURES_MARGIN_LOW",
        "FUTURES_FUNDING_DRIFT",
        "FUTURES_EXTERNAL_ON_MANAGED_PRODUCT",
        "FUTURES_ORPHAN_ORDER",
    }
    assert expected <= {code.value for code in AlertCode}
