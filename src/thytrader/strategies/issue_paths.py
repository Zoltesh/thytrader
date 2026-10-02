"""Document paths and plain messages for strategy validation issues.

Pydantic validates a smart-mode union by trying every member, so one wrong key inside
a condition tree produces a dozen errors whose locations carry member tags that are
not part of the document (``AllCondition``,
``function-after[validate_cross_operands(), ComparisonCondition]``, a discriminator
value such as ``reward_risk``). This module walks the submitted document alongside
each error location, drops those tags, keeps only the union members whose shape
matches the document, and renders paths the author can follow, for example
``entry.when.all[0].left.input`` with ``unknown field "input"``.

The rewrite only changes how an invalid document is explained: validity itself is
still decided by :class:`~thytrader.strategies.models.StrategyDefinition`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import TYPE_CHECKING, Final, cast

if TYPE_CHECKING:
    from pydantic import ValidationError
    from pydantic_core import ErrorDetails

LocPart = str | int
"""One element of a Pydantic error location."""

DOCUMENT_ROOT: Final = "(document)"
_SHAPE_ERRORS: Final = frozenset({"missing", "extra_forbidden"})
_TYPE_ERRORS: Final = frozenset(
    {
        "model_type",
        "model_attributes_type",
        "dict_type",
        "dataclass_type",
        "is_instance_of",
        "list_type",
        "tuple_type",
    }
)
_TYPE_MISMATCH_PENALTY: Final = 100
_MESSAGE_LIMIT: Final = 500
_SHOULD_PREFIX = re.compile(r"^(?:Input|String|Value|List|Tuple|Dictionary|Decimal|Number) should ")
_VALIDATOR_PREFIX = re.compile(r"^(?:Value error|Assertion failed), ")
_TAG_CLASS = re.compile(r"([A-Z][A-Za-z0-9]*)\]?$")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


class _Missing:
    """Marker for a location below a key the document does not contain."""


_MISSING: Final = _Missing()


@dataclass(frozen=True, slots=True)
class DocumentIssue:
    """One validation problem at a document path with a plain-language message."""

    loc: str
    message: str


@dataclass(frozen=True, slots=True)
class _Branch:
    """One union member Pydantic tried at a raw location prefix."""

    prefix: tuple[LocPart, ...]
    tag: str


@dataclass(frozen=True, slots=True)
class _Located:
    """One Pydantic error mapped onto the document."""

    raw: tuple[LocPart, ...]
    path: tuple[LocPart, ...]
    branches: tuple[_Branch, ...]
    kind: str
    message: str
    context: Mapping[str, object]

    def depth_below(self, branch: _Branch) -> int:
        """Return how many raw location parts follow ``branch``'s member tag."""
        return len(self.raw) - len(branch.prefix) - 1


def document_issues(
    error: ValidationError, document: Mapping[str, object], *, limit: int
) -> tuple[DocumentIssue, ...]:
    """Explain one failed validation of ``document`` with document paths.

    Args:
        error: The ``StrategyDefinition`` validation failure.
        document: The submitted document the error locations refer to.
        limit: Maximum number of issues returned.

    Returns:
        Deduplicated issues in Pydantic's order, at most ``limit`` of them.
    """
    located = tuple(_locate(item, document) for item in error.errors(include_url=False))
    scores = _branch_scores(located)
    kept = tuple(item for item in located if _on_best_branches(item, scores))
    issues: list[DocumentIssue] = []
    seen: set[DocumentIssue] = set()
    for issue in _render(kept, scores):
        if issue not in seen:
            seen.add(issue)
            issues.append(issue)
    return tuple(issues[:limit])


def render_path(path: Sequence[LocPart]) -> str:
    """Render ``("entry", "when", "all", 0, "left")`` as ``entry.when.all[0].left``."""
    text = ""
    for part in path:
        separator = "" if isinstance(part, int) or not text else "."
        text = f"{text}{separator}{f'[{part}]' if isinstance(part, int) else part}"
    return text or DOCUMENT_ROOT


def _locate(item: ErrorDetails, document: Mapping[str, object]) -> _Located:
    """Walk ``document`` along one error location, separating keys from member tags."""
    raw = tuple(item["loc"])
    node: object = document
    path: list[LocPart] = []
    branches: list[_Branch] = []
    for position, part in enumerate(raw):
        child = _child(node, part)
        if child is not _MISSING:
            path.append(part)
            node = child
        elif _is_member_tag(part, node):
            branches.append(_Branch(prefix=raw[:position], tag=str(part)))
        else:
            path.append(part)
            node = _MISSING
    context = item.get("ctx")
    return _Located(
        raw=raw,
        path=tuple(path),
        branches=tuple(branches),
        kind=item["type"],
        message=str(item["msg"]),
        context=context if context is not None else {},
    )


def _child(node: object, part: LocPart) -> object:
    """Return the document value addressed by ``part``, or the missing marker."""
    if isinstance(node, Mapping) and isinstance(part, str):
        return cast("Mapping[str, object]", node).get(part, _MISSING)
    if isinstance(node, list | tuple) and isinstance(part, int) and 0 <= part < len(node):
        return cast("Sequence[object]", node)[part]
    return _MISSING


def _is_member_tag(part: LocPart, node: object) -> bool:
    """True when ``part`` names a union member or validator rather than a document key."""
    if isinstance(node, _Missing):
        return isinstance(part, str) and _looks_like_type_tag(part)
    if not isinstance(node, Mapping | list | tuple):
        return True
    if isinstance(node, list | tuple):
        return isinstance(part, str)
    if isinstance(part, int):
        return True
    mapping = cast("Mapping[str, object]", node)
    discriminators = {str(value) for value in mapping.values() if isinstance(value, str | bool)}
    return _looks_like_type_tag(part) or part in discriminators


def _looks_like_type_tag(part: str) -> bool:
    """Schema keys are snake_case; Pydantic member tags are class names or ``kind[...]``."""
    return part[:1].isupper() or any(character in part for character in "[](), ")


def _branch_scores(located: Sequence[_Located]) -> dict[tuple[LocPart, ...], dict[str, int]]:
    """Score every tried member by how badly its shape mismatches the document.

    A member scores one point per required key the document lacks or extra key it has
    directly at that position, and a large penalty when the value is not even an
    object of that member's type. Errors deeper inside a member do not count: they
    are the member's own detailed complaints.
    """
    scores: dict[tuple[LocPart, ...], dict[str, int]] = {}
    for item in located:
        for branch in item.branches:
            members = scores.setdefault(branch.prefix, {})
            depth = item.depth_below(branch)
            penalty = 0
            if depth == 1 and item.kind in _SHAPE_ERRORS:
                penalty = 1
            elif depth == 0 and item.kind in _TYPE_ERRORS:
                penalty = _TYPE_MISMATCH_PENALTY
            members[branch.tag] = members.get(branch.tag, 0) + penalty
    return scores


def _on_best_branches(
    item: _Located, scores: Mapping[tuple[LocPart, ...], Mapping[str, int]]
) -> bool:
    """Keep an error only when every member on its location is a best-matching member."""
    for branch in item.branches:
        members = scores[branch.prefix]
        if members[branch.tag] != min(members.values()):
            return False
    return True


def _tied_prefixes(
    scores: Mapping[tuple[LocPart, ...], Mapping[str, int]],
) -> dict[tuple[LocPart, ...], tuple[str, ...]]:
    """Return union positions where several members match the document equally badly."""
    tied: dict[tuple[LocPart, ...], tuple[str, ...]] = {}
    for prefix, members in scores.items():
        best = min(members.values())
        winners = tuple(tag for tag, score in members.items() if score == best)
        if len(winners) > 1 and best > 0:
            tied[prefix] = winners
    return tied


def _render(
    kept: Sequence[_Located], scores: Mapping[tuple[LocPart, ...], Mapping[str, int]]
) -> list[DocumentIssue]:
    """Render kept errors, folding equally bad union members into one explanation."""
    tied = _tied_prefixes(scores)
    issues: list[DocumentIssue] = []
    emitted: set[tuple[LocPart, ...]] = set()
    for item in kept:
        prefix = _folding_prefix(item, tied)
        if prefix is None:
            issues.append(_plain_issue(item))
            continue
        if prefix in emitted:
            continue
        emitted.add(prefix)
        issues.extend(_tie_issues(prefix, tied[prefix], kept))
    return issues


def _folding_prefix(
    item: _Located, tied: Mapping[tuple[LocPart, ...], tuple[str, ...]]
) -> tuple[LocPart, ...] | None:
    """Return the tied union position this error's shape complaint belongs to, if any."""
    for branch in item.branches:
        if branch.prefix not in tied:
            continue
        depth = item.depth_below(branch)
        if (depth == 1 and item.kind in _SHAPE_ERRORS) or (
            depth == 0 and item.kind in _TYPE_ERRORS
        ):
            return branch.prefix
    return None


def _tie_issues(
    prefix: tuple[LocPart, ...], members: tuple[str, ...], kept: Sequence[_Located]
) -> list[DocumentIssue]:
    """Explain a position no member matched: unknown keys first, then the alternatives."""
    missing: dict[str, list[str]] = {tag: [] for tag in members}
    extra: dict[str, set[str]] = {tag: set() for tag in members}
    position: tuple[LocPart, ...] | None = None
    for item in kept:
        branch = next((value for value in item.branches if value.prefix == prefix), None)
        if branch is None or branch.tag not in missing or item.depth_below(branch) != 1:
            continue
        key = str(item.raw[-1])
        position = item.path[:-1]
        if item.kind == "missing":
            missing[branch.tag].append(key)
        elif item.kind == "extra_forbidden":
            extra[branch.tag].add(key)
    if position is None:
        position = next(
            (item.path for item in kept if any(b.prefix == prefix for b in item.branches)),
            (),
        )
    common = set(extra[members[0]])
    for tag in members[1:]:
        common &= extra[tag]
    unknown = sorted(common)
    issues = [
        DocumentIssue(loc=render_path((*position, key)), message=f'unknown field "{key}"')
        for key in unknown
    ]
    issues.append(
        DocumentIssue(
            loc=render_path(position),
            message=_limit(_alternatives_message(members, missing, extra, common)),
        )
    )
    return issues


def _alternatives_message(
    members: tuple[str, ...],
    missing: Mapping[str, Sequence[str]],
    extra: Mapping[str, set[str]],
    unknown: set[str],
) -> str:
    """Say which shapes the value could take, e.g. ``must be a literal operand (needs ...)``."""
    shapes = " or ".join(
        _member_shape(tag, missing[tag], sorted(extra[tag] - unknown)) for tag in members
    )
    if any(missing[tag] or extra[tag] - unknown for tag in members):
        return f"must be {shapes}"
    return f"must be a JSON object: {shapes}"


def _member_shape(tag: str, missing: Sequence[str], rejected: Sequence[str]) -> str:
    """Describe what one union member would need, e.g. ``a literal operand (needs "literal")``."""
    name = _member_name(tag)
    article = "an" if name[:1] in "aeiou" else "a"
    notes: list[str] = []
    if missing:
        notes.append("needs " + ", ".join(f'"{key}"' for key in missing))
    if rejected:
        notes.append("does not take " + ", ".join(f'"{key}"' for key in rejected))
    if not notes:
        return f"{article} {name}"
    return f"{article} {name} ({'; '.join(notes)})"


def _member_name(tag: str) -> str:
    """Turn ``IndicatorOperand`` or ``function-after[..., ComparisonCondition]`` into words."""
    match = _TAG_CLASS.search(tag)
    if match is None:
        return f"{tag} value"
    return _CAMEL_BOUNDARY.sub(" ", match.group(1)).lower()


def _plain_issue(item: _Located) -> DocumentIssue:
    """Render one error at its document path with a plain message."""
    path = item.path
    if item.kind in {"union_tag_invalid", "union_tag_not_found"}:
        discriminator = str(item.context.get("discriminator", "kind")).strip("'\"")
        path = (*path, discriminator)
    return DocumentIssue(loc=render_path(path), message=_limit(_plain_message(item)))


def _plain_message(item: _Located) -> str:
    """Rephrase one Pydantic message without Pydantic's own vocabulary."""
    key = item.path[-1] if item.path else None
    if item.kind == "missing":
        return f'"{key}" is required' if isinstance(key, str) else "a value is required"
    if item.kind == "extra_forbidden":
        return f'unknown field "{key}"'
    if item.kind == "union_tag_invalid":
        expected = item.context.get("expected_tags", "")
        return f"must be one of {expected} (got {item.context.get('tag')!r})"
    if item.kind == "union_tag_not_found":
        return "is required to choose the kind of object"
    if item.kind in {"list_type", "tuple_type"}:
        return "must be a JSON array"
    if item.kind in _TYPE_ERRORS:
        return "must be a JSON object"
    message = _VALIDATOR_PREFIX.sub("", item.message)
    return _SHOULD_PREFIX.sub("must ", message)


def _limit(message: str) -> str:
    """Bound one message to the stored issue size."""
    return message[:_MESSAGE_LIMIT]
