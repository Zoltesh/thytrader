"""Manifest-last immutable replay publication and consumer-time reverification."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from typing import Literal

from pydantic import Field, JsonValue, TypeAdapter

from thytrader.replay.codec import validate_no_coercion
from thytrader.replay.models import FrozenModel, ReplayPackage, canonical_bytes, fingerprint

__all__ = ["MAX_PACKAGE_BYTES", "Manifest", "load", "publish", "read_input", "validate_no_coercion"]

MAX_PACKAGE_BYTES = 16 * 1024 * 1024


class Manifest(FrozenModel):
    """One fixed-name payload; no caller-selected transport paths."""

    schema_version: Literal["thytrader-replay-manifest-v1"]
    fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    payload: Literal["evidence.json"]
    size: int = Field(ge=1, le=MAX_PACKAGE_BYTES)


def _sync_directory(path: Path) -> None:
    """Flush publication ordering on filesystems that honor directory fsync."""
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, content: bytes) -> None:
    """Flush a same-directory temporary file before atomic replacement."""
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".publishing-")
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.replace(path)
        _sync_directory(path.parent)
    finally:
        temporary_path.unlink(missing_ok=True)


def publish(root: Path, package: ReplayPackage) -> Path:
    """Publish all evidence before the manifest; orphan directories are not reusable."""
    content = canonical_bytes(package)
    if len(content) > MAX_PACKAGE_BYTES:
        raise ValueError("replay package exceeds the byte budget")
    digest = fingerprint(content)
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = root / digest.removeprefix("sha256:")
    manifest_path = directory / "manifest.json"
    if directory.exists():
        existing, _ = load(manifest_path)
        if canonical_bytes(existing) != content:
            raise ValueError("immutable publication collision")
        return manifest_path
    directory.mkdir(mode=0o700)
    _sync_directory(root)
    _atomic_write(directory / "evidence.json", content)
    manifest = Manifest(
        schema_version="thytrader-replay-manifest-v1",
        fingerprint=digest,
        payload="evidence.json",
        size=len(content),
    )
    _atomic_write(manifest_path, manifest.model_dump_json().encode("utf-8"))
    return manifest_path


def _bounded_read(path: Path) -> bytes:
    """Reject symlinks and bound allocation before parsing untrusted evidence."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_PACKAGE_BYTES:
        raise ValueError("invalid replay artifact path or size")
    with path.open("rb") as stream:
        content = stream.read(MAX_PACKAGE_BYTES + 1)
    if len(content) > MAX_PACKAGE_BYTES:
        raise ValueError("replay artifact exceeds the byte budget")
    return content


def load(manifest_path: Path) -> tuple[ReplayPackage, Manifest]:
    """Reverify exact bytes, canonical location and fixed payload containment in one read."""
    if manifest_path.name != "manifest.json" or manifest_path.parent.is_symlink():
        raise ValueError("invalid replay manifest location")
    manifest = Manifest.model_validate_json(_bounded_read(manifest_path))
    directory = manifest_path.parent
    if directory.name != manifest.fingerprint.removeprefix("sha256:"):
        raise ValueError("manifest location does not equal evidence identity")
    payload_path = directory / manifest.payload
    if payload_path.resolve().parent != directory.resolve():
        raise ValueError("replay payload escapes publication directory")
    content = _bounded_read(payload_path)
    if len(content) != manifest.size or fingerprint(content) != manifest.fingerprint:
        raise ValueError("replay payload failed integrity verification")
    package = ReplayPackage.model_validate_json(content)
    if canonical_bytes(package) != content:
        raise ValueError("replay payload is not canonical")
    return package, manifest


def read_input(path: Path) -> ReplayPackage:
    """Validate contributor-supplied normalized JSON without reading credentials or settings."""
    content = _bounded_read(path)

    # Duplicate fields cannot be hidden behind JSON's last-key-wins behavior.
    def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        """Refuse ambiguous duplicate JSON object keys."""
        output: dict[str, object] = {}
        for key, value in pairs:
            if key in output:
                raise ValueError("duplicate JSON field")
            output[key] = value
        return output

    json.loads(content, object_pairs_hook=unique_pairs)
    if re.search(rb'"(?:password|authorization|api_key|private_key|headers)"\s*:', content):
        raise ValueError("secret transport fields are forbidden")
    package = ReplayPackage.model_validate_json(content)
    validate_no_coercion(
        TypeAdapter(JsonValue).validate_json(content),
        TypeAdapter(JsonValue).validate_json(package.model_dump_json(by_alias=True)),
    )
    return package
