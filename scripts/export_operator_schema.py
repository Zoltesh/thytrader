"""Export the shipped operator schema from the same models the HTTP CLI validates.

Run from the repository root with ``uv run python scripts/export_operator_schema.py``.
``--check`` verifies the committed artifact without changing it. No stores, credentials,
HTTP requests, or application startup are involved.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from pydantic.json_schema import JsonSchemaValue, models_json_schema

from thytrader.operator.http import _REPORT_MODELS
from thytrader.operator.models import REPORT_KINDS, OperatorEnvelope

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills/thytrader-operator/references/operator-report-v1.schema.json"
)


def operator_schema() -> JsonSchemaValue:
    """Bind each discriminated report to its complete payload and nested model schemas.

    JSON Schema is a dynamic serialization boundary (Pydantic's JsonSchemaValue).
    Report classes are the typed, authoritative HTTP registry; all definitions are
    generated together so colliding model names receive consistent references.
    """
    models = tuple(dict.fromkeys(_REPORT_MODELS.values()))
    references, definitions = models_json_schema([(model, "validation") for model in models])
    schema = OperatorEnvelope.model_json_schema()
    schema["$defs"] = definitions["$defs"]
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://thytrader.dev/schemas/operator-report-v1.json"
    schema["title"] = "ThyTrader operator reports (generated; do not edit by hand)"
    schema["properties"]["report_kind"] = {"type": "string", "enum": list(REPORT_KINDS)}
    schema["properties"]["payload"] = {"type": "object"}
    schema["required"] = [*schema["required"], "report_kind", "payload"]
    schema["anyOf"] = [references[(model, "validation")] for model in models]
    return schema


def rendered_schema() -> str:
    """Return deterministic JSON with a trailing newline for reviewable generated diffs."""
    return json.dumps(operator_schema(), indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    """Write the schema or fail a drift check, without starting the application."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Fail if the committed schema is stale."
    )
    parser.add_argument("--output", type=Path, default=_SCHEMA_PATH)
    arguments = parser.parse_args()
    output: Path = arguments.output
    expected = rendered_schema()
    if arguments.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != expected:
            sys.stderr.write(f"Stale generated operator schema: {output}\n")
            return 1
        sys.stdout.write("Operator schema matches the HTTP report models.\n")
        return 0
    output.write_text(expected, encoding="utf-8")
    sys.stdout.write(f"Updated {output}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
