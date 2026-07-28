#!/usr/bin/env python3
"""Compatibility entry point for the renamed local-stack bootstrap helper."""

from scripts.setup_local_stack import configure_local_database, main

__all__ = ["configure_local_database", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
