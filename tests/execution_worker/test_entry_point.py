"""Packaging check for the dedicated execution worker."""

from pathlib import Path
import sys


def test_execution_worker_installed_entry_point_exists() -> None:
    """Packaging must install the separately supervised execution worker executable."""
    executable = Path(sys.executable).parent / "thytrader-execution-worker"
    assert executable.is_file()
