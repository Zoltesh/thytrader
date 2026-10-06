"""Explicit fleet disarm, managed stop, and flatten controls.

The package orchestrates existing deployment services. It does not submit
broker orders and does not claim a venue transaction across books.
"""

from __future__ import annotations

__all__ = ["ENTRY_INHIBITED_PREFIX"]

ENTRY_INHIBITED_PREFIX = "ENTRY_INHIBITED"
