#!/usr/bin/env python3
"""Regenerate the browser builder's catalogs from the Python registries.

Run from the repository root after changing the indicator registry
(``src/thytrader/strategies/indicator_catalog.py``), the strategy schema, or the
research templates:

    uv run python scripts/export_indicator_catalog.py

``tests/strategies/test_indicator_catalog.py`` fails when a checked-in file under
``web/src/lib/generated/`` drifts from the registries.
"""

from pathlib import Path

from thytrader.web_catalog import WEB_GENERATED_DIRECTORY, render_web_catalog_files


def main() -> int:
    """Write every generated catalog file and return a process exit code."""
    directory = Path(__file__).resolve().parents[1].joinpath(*WEB_GENERATED_DIRECTORY)
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in render_web_catalog_files().items():
        (directory / name).write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
