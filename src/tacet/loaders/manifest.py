"""Strict manifest ingestion with a hand-rolled draft-07 subset validator.

The manifest (``manifest.json`` at the repo root) is tracked in git while
the recordings under ``data/`` are not; each entry carries the sha256 of
its channel files so the tracked index can prove what was recorded.
"""

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

_SCALAR_TYPES = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def _repo_root(start=None) -> Path:
    """Nearest ancestor (including start) that contains pyproject.toml."""
    p = Path(start if start is not None else Path.cwd()).resolve()
    for candidate in (p, *p.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return p


def compute_sha256(path: str, chunk_bytes: int = 1 << 20) -> str:
    """Return the hex sha256 of a file, read in chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_bytes), b""):
            h.update(chunk)
    return h.hexdigest()


def _validate_against_schema(value: Any, schema: dict, where: str) -> None:
    """Validate ``value`` against the draft-07 subset used by our schema."""
    if "enum" in schema:
        if value not in schema["enum"]:
            raise ValueError(f"{where}: {value!r} not in allowed enum {schema['enum']}")
    expected = schema.get("type")
    if expected is not None:
        py = _SCALAR_TYPES[expected]
        if expected == "number" and isinstance(value, bool):
            raise ValueError(f"{where}: expected number, got bool")
        if not isinstance(value, py):
            raise ValueError(
                f"{where}: expected {expected}, got {type(value).__name__}"
            )
    if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
        raise ValueError(f"{where}: must be > {schema['exclusiveMinimum']}")
    if "minLength" in schema and len(value) < schema["minLength"]:
        raise ValueError(f"{where}: shorter than {schema['minLength']}")
    if "pattern" in schema:
        import re

        if re.fullmatch(schema["pattern"], value) is None:
            raise ValueError(f"{where}: does not match {schema['pattern']}")
    if expected == "object":
        for req in schema.get("required", []):
            if req not in value:
                raise ValueError(f"{where}: missing required field '{req}'")
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                _validate_against_schema(value[key], sub, f"{where}.{key}")
    if expected == "array":
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ValueError(f"{where}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ValueError(f"{where}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, item in enumerate(value):
                _validate_against_schema(item, schema["items"], f"{where}[{i}]")


def validate_entry(entry: dict, schema_path: Optional[str] = None) -> None:
    """Validate a manifest entry against the schema; raise ValueError if bad."""
    if schema_path is None:
        schema_path = str(_repo_root() / "manifest.schema.json")
    if not isinstance(entry, dict):
        raise ValueError("entry must be an object")
    with open(schema_path, encoding="utf-8") as fh:
        schema = json.load(fh)
    _validate_against_schema(entry, schema, "entry")
    try:
        datetime.fromisoformat(entry["timestamp"])
    except ValueError as exc:
        raise ValueError(f"entry.timestamp: not ISO 8601 ({exc})") from exc
    for i, ch in enumerate(entry["channels"]):
        if not ch["path"].startswith("data/"):
            raise ValueError(f"entry.channels[{i}].path: must live under data/")


def append_entry(entry: dict, manifest_path: Optional[str] = None) -> None:
    """Validate and append ``entry``; reject duplicate ids."""
    if manifest_path is None:
        manifest_path = str(_repo_root() / "manifest.json")
    validate_entry(entry)
    entries: list = []
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as fh:
            entries = json.load(fh)
    if any(e.get("id") == entry["id"] for e in entries):
        raise ValueError(f"duplicate recording id: {entry['id']}")
    entries.append(entry)
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2)
        fh.write("\n")


def query(
    manifest_path: Optional[str] = None,
    schema_path: Optional[str] = None,
    **equality_filters: Any,
) -> list:
    """Return manifest entries whose fields equal every given filter.

    Nested filters use ``__`` as separator, e.g. ``gain__mode='manual'``.
    """
    if manifest_path is None:
        manifest_path = str(_repo_root() / "manifest.json")
    with open(manifest_path, encoding="utf-8") as fh:
        entries = json.load(fh)
    rows = []
    for e in entries:
        ok = True
        for key, want in equality_filters.items():
            node: Any = e
            for part in key.split("__"):
                if not isinstance(node, dict) or part not in node:
                    ok = False
                    break
                node = node[part]
            if not ok or node != want:
                ok = False
                break
        if ok:
            rows.append(e)
    return rows
