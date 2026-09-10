"""Gap classification without interpolation."""

from thytrader.data_control.models import GapCause, classify_gap


def test_classify_gap_never_invents_a_present_bar() -> None:
    """Local complete coverage is not reported as a gap."""
    assert (
        classify_gap(
            present_locally=True,
            present_on_exchange=True,
            worker_attempted=True,
            worker_complete=True,
        )
        is None
    )


def test_classify_gap_distinguishes_fetch_exchange_and_incomplete() -> None:
    """Missing bars keep an explicit cause instead of an interpolated price."""
    assert (
        classify_gap(
            present_locally=False,
            present_on_exchange=False,
            worker_attempted=False,
            worker_complete=False,
        )
        is GapCause.EXCHANGE_UNAVAILABLE
    )
    assert (
        classify_gap(
            present_locally=False,
            present_on_exchange=True,
            worker_attempted=True,
            worker_complete=False,
        )
        is GapCause.INCOMPLETE_LOCAL
    )
    assert (
        classify_gap(
            present_locally=False,
            present_on_exchange=True,
            worker_attempted=False,
            worker_complete=False,
        )
        is GapCause.NOT_FETCHED
    )
