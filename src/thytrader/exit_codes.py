"""Process exit codes shared by every agent CLI.

This module imports nothing from ThyTrader, so any lane CLI can use the codes
without depending on the operator package. ``thytrader.operator.status``
re-exports them.
"""

EXIT_HEALTHY = 0
EXIT_DEGRADED = 1
EXIT_FAILED = 2
EXIT_USAGE = 3
