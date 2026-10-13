"""Supported offline inspect/export CLI with stable JSON and meaningful refusal codes."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

from pydantic import ValidationError

from thytrader.replay.acceptance import check_expected
from thytrader.replay.models import ReplayPackage, canonical_bytes, fingerprint
from thytrader.replay.packages import Manifest, load, publish, read_input
from thytrader.replay.report import render
from thytrader.replay.runner import ReplayReport, report_exit_code, run


def main(argv: Sequence[str] | None = None) -> int:
    """Inspect or publish evidence without constructing settings, database or network clients."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    schema = commands.add_parser(
        "schema", help="Export a language-neutral serialization JSON Schema"
    )
    schema.add_argument("--kind", choices=("package", "manifest", "report"), default="package")
    inspect = commands.add_parser("inspect", help="Reverify immutable evidence and disclose gaps")
    inspect.add_argument("manifest", type=Path)
    execute = commands.add_parser("run", help="Replay offline; missing evidence exits 3")
    execute.add_argument("manifest", type=Path)
    execute.add_argument("--format", choices=("json", "text"), default="json")
    execute.add_argument(
        "--expected", type=Path, help="Check an external golden report; acceptance is not parity"
    )
    export = commands.add_parser("export", help="Publish normalized private or synthetic JSON")
    export.add_argument("--input", type=Path, required=True)
    export.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "schema":
            model = {"package": ReplayPackage, "manifest": Manifest, "report": ReplayReport}[
                args.kind
            ]
            sys.stdout.write(
                json.dumps(model.model_json_schema(mode="serialization"), sort_keys=True) + "\n"
            )
            return 0
        if args.command == "run":
            package, _manifest = load(args.manifest)
            report = asyncio.run(run(package))
            if args.expected is not None:
                report = check_expected(report, args.expected)
            sys.stdout.write(
                render(report) if args.format == "text" else report.model_dump_json() + "\n"
            )
            if report.acceptance is not None:
                return 0 if report.acceptance.status == "match" else 1
            return report_exit_code(report)
        if args.command == "export":
            package = read_input(args.input)
            manifest_path = publish(args.root, package)
            # Read back the exact external publication before claiming success.
            _verified, manifest = load(manifest_path)
            output = {"fingerprint": manifest.fingerprint, "manifest": str(manifest_path)}
        else:
            package, manifest = load(args.manifest)
            output = {
                "fingerprint": fingerprint(canonical_bytes(package)),
                "evidence_kind": package.evidence_kind,
                "execution_completeness": "unavailable",
                "unavailable_reasons": list(package.unavailable_reasons),
                "ledger_fill_count": len(package.ledger.fills) if package.ledger else 0,
            }
        sys.stdout.write(json.dumps(output, sort_keys=True) + "\n")
    except OSError, TypeError, ValueError, ValidationError:
        sys.stderr.write("thytrader-replay: evidence rejected or unavailable\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
