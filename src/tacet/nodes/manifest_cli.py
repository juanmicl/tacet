"""`tacet manifest` subcommand: validate the recording manifest (v2).

Checks the manifest document against ``manifest.schema.json`` (every
recording), enforces id uniqueness and — with ``--check-files`` — verifies
each channel file exists and its sha256 matches the recorded one. File
checks are skipped gracefully when the repo has no ``data/`` directory
(e.g. a fresh clone of the public research core).
"""

import argparse
import json
import sys
from pathlib import Path

from tacet.loaders import manifest as manifest_mod


def validate_manifest(doc, check_files: bool = False,
                      repo_root: Path | None = None) -> list:
    """Return a list of problems (empty list = the manifest is valid)."""
    errors: list = []
    if not isinstance(doc, dict) or not isinstance(doc.get("recordings"), list):
        return ["manifest must be a v2 wrapper object "
                "{schema_version, recordings}"]
    if doc.get("schema_version") != "2.0":
        errors.append(
            f"schema_version: expected '2.0', got {doc.get('schema_version')!r}")
    root = Path(repo_root) if repo_root is not None else manifest_mod._repo_root()
    seen_ids: set = set()
    files_present = (root / "data").is_dir()
    if check_files and not files_present:
        print("note: data/ not present; skipping file checks", file=sys.stderr)
    for i, entry in enumerate(doc["recordings"]):
        rid = entry.get("id", f"#{i}") if isinstance(entry, dict) else f"#{i}"
        try:
            manifest_mod.validate_entry(entry)
        except ValueError as exc:
            errors.append(f"recordings[{i}] ({rid}): {exc}")
        if rid in seen_ids:
            errors.append(f"recordings[{i}]: duplicate id {rid!r}")
        seen_ids.add(rid)
        if check_files and files_present and isinstance(entry, dict):
            for j, ch in enumerate(entry.get("channels", [])):
                fpath = root / ch.get("path", "")
                if not fpath.is_file():
                    errors.append(
                        f"recordings[{i}] ({rid}) channel {j}: "
                        f"file missing: {ch.get('path')}")
                    continue
                digest = manifest_mod.compute_sha256(str(fpath))
                if digest != ch.get("sha256"):
                    errors.append(
                        f"recordings[{i}] ({rid}) channel {j}: "
                        f"sha256 mismatch for {ch.get('path')}")
    return errors


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="tacet manifest",
        description="Manifest tooling (v2 wrapper format).")
    parser.add_argument("command", choices=["validate"],
                        help="validate the manifest against the v2 schema")
    parser.add_argument(
        "--check-files", action="store_true",
        help="verify each channel file exists and its sha256 matches "
             "(skipped when data/ is absent)")
    parser.add_argument(
        "--manifest", default=None,
        help="manifest path (default: <repo root>/manifest.json)")
    args = parser.parse_args(argv)
    assert args.command == "validate"

    path = (Path(args.manifest) if args.manifest
            else Path(manifest_mod._repo_root()) / "manifest.json")
    if not path.is_file():
        print(f"error: manifest not found: {path}", file=sys.stderr)
        return 1
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"error: {path} is not valid JSON: {exc}", file=sys.stderr)
        return 1
    errors = validate_manifest(doc, check_files=args.check_files)
    n = len(doc.get("recordings", [])) if isinstance(doc, dict) else 0
    if errors:
        for problem in errors:
            print(f"error: {problem}", file=sys.stderr)
        print(f"{path}: FAILED ({len(errors)} problem(s), "
              f"{n} recordings)", file=sys.stderr)
        return 1
    print(f"{path}: OK (schema 2.0, {n} recordings)")
    return 0
