"""Package layering: imports point down the declared layers, and upward imports only shrink.

``package_layers.json`` declares the intended architecture as layers, highest first. A
module belongs to the component its top-level package names, unless a ``components``
pattern (an ``fnmatch`` glob over the dotted module path without ``thytrader.``) claims it
first: agent CLIs and their loopback clients live beside their domain but sit at the top.

A module may import any module in its own layer or a lower one. Every upward import is a
dependency cycle waiting to happen and is listed in ``allowed_upward_imports`` as
``"<from component> -> <to component>"`` with a ceiling: the number of distinct
(importing module, imported module) pairs when the entry was recorded. A ceiling never
goes up and must come down as soon as the count does; delete the entry at zero. Imports
under ``TYPE_CHECKING`` and inside functions count too: they are still dependencies.
Same-layer components must not import each other in a cycle.

Run ``uv run python -m tests.test_package_layers`` to print the current upward imports.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import cache
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "thytrader"
CONFIG = Path(__file__).with_name("package_layers.json")
ROOT_COMPONENT = "thytrader"


@dataclass(frozen=True)
class LayerConfig:
    """Parsed ``package_layers.json``.

    Attributes:
        patterns: ``(glob, component)`` overrides, checked in file order.
        layer_of: Component name to layer index, 0 being the highest layer.
        layer_names: Layer names, highest first.
        allowed: ``"<from> -> <to>"`` upward edge to its import-pair ceiling.
    """

    patterns: tuple[tuple[str, str], ...]
    layer_of: dict[str, int]
    layer_names: tuple[str, ...]
    allowed: dict[str, int]


@cache
def _config() -> LayerConfig:
    raw = json.loads(CONFIG.read_text(encoding="utf-8"))
    patterns = tuple(
        (str(pattern), str(component))
        for component, globs in raw["components"].items()
        for pattern in globs
    )
    layer_of: dict[str, int] = {}
    names: list[str] = []
    for index, layer in enumerate(raw["layers"]):
        names.append(str(layer["name"]))
        for component in layer["components"]:
            assert component not in layer_of, f"{component} is declared in two layers"
            layer_of[str(component)] = index
    allowed = {str(edge): int(ceiling) for edge, ceiling in raw["allowed_upward_imports"].items()}
    return LayerConfig(patterns, layer_of, tuple(names), allowed)


@cache
def _modules() -> dict[str, Path]:
    """Map every dotted module under ``thytrader`` (without the prefix) to its file."""
    modules: dict[str, Path] = {}
    for path in PACKAGE.rglob("*.py"):
        parts = list(path.relative_to(PACKAGE).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        modules[".".join(parts)] = path
    return modules


def component_of(module: str) -> str:
    """Return the component a dotted module (without ``thytrader.``) belongs to."""
    for pattern, component in _config().patterns:
        if fnmatchcase(module, pattern):
            return component
    return module.split(".", 1)[0] if module else ROOT_COMPONENT


def _resolve(target: str) -> str | None:
    """Return the longest known module prefix of an absolute ``thytrader`` import."""
    if target != "thytrader" and not target.startswith("thytrader."):
        return None
    name = target.removeprefix("thytrader").removeprefix(".")
    modules = _modules()
    while name and name not in modules:
        name = name.rpartition(".")[0]
    return name


def _imported_names(module: str, path: Path) -> set[str]:
    """Return the absolute dotted names a module imports, including submodule candidates."""
    package = module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)]
                source = ".".join(["thytrader", *base, *([node.module] if node.module else [])])
            else:
                source = node.module or ""
            names.add(source)
            names.update(f"{source}.{alias.name}" for alias in node.names)
    return names


@cache
def import_pairs() -> frozenset[tuple[str, str]]:
    """Return every distinct ``(importer, imported)`` pair between ``thytrader`` modules."""
    pairs: set[tuple[str, str]] = set()
    for module, path in _modules().items():
        for name in _imported_names(module, path):
            target = _resolve(name)
            if target is not None and target != module:
                pairs.add((module, target))
    return frozenset(pairs)


def upward_imports() -> dict[str, list[tuple[str, str]]]:
    """Group import pairs that point to a higher layer by ``"<from> -> <to>"`` edge."""
    layer_of = _config().layer_of
    edges: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for importer, imported in sorted(import_pairs()):
        source, target = component_of(importer), component_of(imported)
        if source in layer_of and target in layer_of and layer_of[target] < layer_of[source]:
            edges[f"{source} -> {target}"].append((importer, imported))
    return dict(edges)


def _reachable(graph: dict[str, set[str]], start: str) -> set[str]:
    """Return every component reachable from ``start`` along one or more imports."""
    seen: set[str] = set()
    frontier = list(graph[start])
    while frontier:
        node = frontier.pop()
        if node not in seen:
            seen.add(node)
            frontier.extend(graph[node])
    return seen


def _same_layer_cycles() -> list[tuple[str, str]]:
    """Return same-layer component pairs that import each other, directly or transitively."""
    layer_of = _config().layer_of
    graph: dict[str, set[str]] = defaultdict(set)
    for importer, imported in import_pairs():
        source, target = component_of(importer), component_of(imported)
        if source != target and layer_of.get(source, -1) == layer_of.get(target, -2):
            graph[source].add(target)
    reach = {node: _reachable(graph, node) for node in list(graph)}
    return sorted(
        (first, second)
        for first, targets in reach.items()
        for second in targets
        if first < second and first in reach.get(second, set())
    )


def test_every_component_is_assigned_to_a_layer() -> None:
    """A new top-level package must be placed in a layer before it can be imported."""
    layer_of = _config().layer_of
    unplaced = sorted({component_of(module) for module in _modules()} - set(layer_of))
    assert not unplaced, f"Add these components to a layer in {CONFIG.name}: {unplaced}"


def test_component_patterns_all_match_a_module() -> None:
    """A pattern that matches nothing is stale and hides intent."""
    modules = list(_modules())
    stale = [
        pattern
        for pattern, _ in _config().patterns
        if not any(fnmatchcase(module, pattern) for module in modules)
    ]
    assert not stale, f"Remove component patterns that match no module: {stale}"


def test_upward_imports_stay_within_their_ceilings() -> None:
    """Imports may only point down; listed upward edges may not grow (AGENTS.md §3a)."""
    allowed = _config().allowed
    problems: list[str] = []
    for edge, pairs in sorted(upward_imports().items()):
        ceiling = allowed.get(edge)
        shown = "; ".join(f"{a} -> {b}" for a, b in pairs[:5])
        if ceiling is None:
            problems.append(f"{edge}: new upward import ({len(pairs)}): {shown}")
        elif len(pairs) > ceiling:
            problems.append(f"{edge}: {len(pairs)} upward imports exceed {ceiling}: {shown}")
    assert not problems, (
        "Move the code to the layer that owns it or invert the dependency; never raise a "
        "ceiling.\n" + "\n".join(problems)
    )


def test_upward_import_ceilings_ratchet_down() -> None:
    """A ceiling above the current count must be lowered, and a cleared edge deleted."""
    current = {edge: len(pairs) for edge, pairs in upward_imports().items()}
    stale = [
        f"{edge}: ceiling {ceiling} but {current.get(edge, 0)} remain; "
        + ("delete the entry." if edge not in current else "lower the ceiling.")
        for edge, ceiling in sorted(_config().allowed.items())
        if current.get(edge, 0) < ceiling
    ]
    assert not stale, "\n".join(stale)


def test_same_layer_components_do_not_import_each_other_in_a_cycle() -> None:
    """Peers in one layer may depend on each other only one way."""
    cycles = _same_layer_cycles()
    assert not cycles, f"Same-layer import cycles: {cycles}"


def main() -> None:
    """Print the upward imports and their ceilings, highest edge count first."""
    config = _config()
    edges = upward_imports()
    total = sum(len(pairs) for pairs in edges.values())
    lines = [f"{len(edges)} upward edges, {total} import pairs"]
    for edge, pairs in sorted(edges.items(), key=lambda item: (-len(item[1]), item[0])):
        lines.append(f"{len(pairs):4d} (ceiling {config.allowed.get(edge, 0)})  {edge}")
        lines.extend(f"       {importer} -> {imported}" for importer, imported in pairs)
    sys.stdout.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
