"""Run every tests/test_*.py module; exit nonzero on any failure."""

import runpy
import sys
from pathlib import Path

rc_all = 0
for path in sorted(Path(__file__).parent.glob("test_*.py")):
    print(f"=== {path.stem} ===")
    try:
        runpy.run_module(f"tests.{path.stem}", run_name="__main__")
    except SystemExit as exc:
        if exc.code:
            rc_all = rc_all or 1
sys.exit(rc_all)
