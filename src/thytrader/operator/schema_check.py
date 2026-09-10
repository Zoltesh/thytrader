"""Compatibility check between shipped operator skills and the v1 schema."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from thytrader.operator.models import OPERATOR_API_PREFIX, REPORT_KINDS, SCHEMA_VERSION

_SKILL_RELATIVE = Path("skills") / "thytrader-operator"
_SCHEMA_RELATIVE = _SKILL_RELATIVE / "references" / "operator-report-v1.schema.json"
_DOC_RELATIVES = (
    _SKILL_RELATIVE / "SKILL.md",
    _SKILL_RELATIVE / "references" / "diagnostics-api.md",
    _SKILL_RELATIVE / "references" / "report-schemas.md",
    _SCHEMA_RELATIVE,
)


class SchemaCheckError(RuntimeError):
    """Report that skill docs drifted from the application schema."""


@dataclass(frozen=True, slots=True)
class SchemaCheckResult:
    """Stable schema-check payload for CLI JSON."""

    ok: bool
    schema_version: str
    report_kinds: tuple[str, ...]
    schema_path: str


def repository_root() -> Path:
    """Find the checkout that contains the operator skill files."""
    cwd = Path.cwd()
    if _skill_present(cwd):
        return cwd
    from_module = Path(__file__).resolve().parents[3]
    if _skill_present(from_module):
        return from_module
    raise SchemaCheckError("Could not find skills/thytrader-operator in this checkout.")


def check_operator_schema(root: Path | None = None) -> SchemaCheckResult:
    """Verify schema_version, report kinds, and HTTP paths stay aligned."""
    resolved = root or repository_root()
    schema_path = resolved / _SCHEMA_RELATIVE
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SchemaCheckError("Operator JSON Schema is missing or invalid.") from error
    const = _schema_version_const(schema)
    if const != SCHEMA_VERSION:
        raise SchemaCheckError(
            f"JSON Schema const {const!r} does not match SCHEMA_VERSION {SCHEMA_VERSION!r}."
        )
    kinds = _schema_report_kinds(schema)
    missing_kinds = tuple(kind for kind in REPORT_KINDS if kind not in kinds)
    if missing_kinds:
        raise SchemaCheckError(f"JSON Schema is missing report kinds: {missing_kinds}.")
    combined = _read_docs(resolved)
    if SCHEMA_VERSION not in combined:
        raise SchemaCheckError("Operator skill docs omit the live schema_version.")
    if OPERATOR_API_PREFIX not in combined:
        raise SchemaCheckError("Operator skill docs omit the diagnostics HTTP prefix.")
    for kind in REPORT_KINDS:
        if kind not in combined:
            raise SchemaCheckError(f"Operator skill docs omit report kind {kind!r}.")
    for suffix in (
        "/health",
        "/configuration",
        "/exchange",
        "/market-data",
        "/strategies",
        "/performance",
        "/risk",
        "/reconciliation",
        "/runtime",
        "/data-catalog",
        "/products",
        "/indicators",
        "/support-bundle",
    ):
        if f"{OPERATOR_API_PREFIX}{suffix}" not in combined:
            raise SchemaCheckError(f"Operator skill docs omit {OPERATOR_API_PREFIX}{suffix}.")
    return SchemaCheckResult(
        ok=True,
        schema_version=SCHEMA_VERSION,
        report_kinds=REPORT_KINDS,
        schema_path=str(_SCHEMA_RELATIVE),
    )


def _skill_present(root: Path) -> bool:
    """True when the operator skill contract exists under root."""
    return (root / _SKILL_RELATIVE / "SKILL.md").is_file()


def _read_docs(root: Path) -> str:
    """Concatenate skill contract files used for compatibility checks."""
    parts: list[str] = []
    for relative in _DOC_RELATIVES:
        path = root / relative
        try:
            parts.append(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise SchemaCheckError(f"Missing operator skill file: {relative}.") from error
    return "\n".join(parts)


def _schema_version_const(schema: object) -> str:
    """Read the committed schema_version const."""
    if not isinstance(schema, dict):
        raise SchemaCheckError("Operator JSON Schema must be an object.")
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise SchemaCheckError("Operator JSON Schema is missing properties.")
    version = properties.get("schema_version")
    if not isinstance(version, dict):
        raise SchemaCheckError("Operator JSON Schema is missing schema_version.")
    const = version.get("const")
    if not isinstance(const, str):
        raise SchemaCheckError("Operator JSON Schema schema_version const is missing.")
    return const


def _schema_report_kinds(schema: object) -> set[str]:
    """Read the committed report_kind enum."""
    if not isinstance(schema, dict):
        raise SchemaCheckError("Operator JSON Schema must be an object.")
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise SchemaCheckError("Operator JSON Schema is missing properties.")
    kind = properties.get("report_kind")
    if not isinstance(kind, dict):
        raise SchemaCheckError("Operator JSON Schema is missing report_kind.")
    enum_values = kind.get("enum")
    if not isinstance(enum_values, list):
        raise SchemaCheckError("Operator JSON Schema report_kind enum is missing.")
    return {value for value in enum_values if isinstance(value, str)}
