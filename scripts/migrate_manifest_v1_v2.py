#!/usr/bin/env python3
"""Migrate manifest.json from v1 (flat list) to v2 (wrapper). Idempotent.

Usage:
    uv run python scripts/migrate_manifest_v1_v2.py [--manifest PATH]

Rules and idempotency guarantees live in tacet.loaders.migration. Ids,
paths and checksums are never modified; data/ files are never touched.
"""

import argparse
import json
import sys
from pathlib import Path

from tacet.loaders import migration
from tacet.loaders.manifest import _repo_root


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate a manifest from v1 (flat list) to v2 (wrapper).")
    parser.add_argument("--manifest", default=None,
                        help="manifest path (default: <repo root>/manifest.json)")
    args = parser.parse_args(argv)

    path = (Path(args.manifest) if args.manifest
            else Path(_repo_root()) / "manifest.json")
    before = path.read_text(encoding="utf-8")
    migrated = migration.migrate_manifest(json.loads(before))
    after = json.dumps(migrated, indent=2) + "\n"
    if after == before:
        print(f"{path}: already v2, no changes")
        return 0
    path.write_text(after, encoding="utf-8")
    print(f"{path}: migrated to v2 ({len(migrated['recordings'])} recordings)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
