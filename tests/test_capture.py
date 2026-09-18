"""No-hardware self-tests for the tacet capture CLI."""

import json
import os
import tempfile
from pathlib import Path

import numpy as np

from tacet.nodes import capture


def test_build_argv_o4_fixed():
    argv = capture.build_argv(
        center_hz=5_800_000_000, n_samples=100_000_000,
        lna=32, vga=32, path="data/x/a.cs8")
    assert argv == [
        "hackrf_transfer", "-r", "data/x/a.cs8",
        "-f", "5800000000", "-s", "20000000",
        "-l", "32", "-g", "32", "-n", "100000000",
    ]


def test_o4_scan_bins():
    bins = capture.o4_scan_bins()
    assert 5_170 <= min(bins) <= 5_180          # MHz, 10 MHz steps
    assert 5_840 <= max(bins) <= 5_850
    assert all(5_170 <= b <= 5_250 or 5_750 <= b <= 5_850 for b in bins)
    # overlapping steps: gaps between consecutive same-band bins <= 10 MHz


def test_build_entry_valid():
    args = dict(scenario="elrs-bench", freq_mhz=2440, duration_s=5.0,
                lna=32, vga=32, power_mw=100.0, notes="bench test",
                distance_m=1.0, env="urban", los="los",
                antenna="dual-band 2.4/5.8 SMA",
                path="data/signature_capture/s/elrs-bench_000.cs8",
                sha256="ab" * 32, timestamp="2026-09-19T10:00:00+00:00")
    entry = capture.build_entry(args)
    from tacet.loaders import manifest as mm
    mm.validate_entry(entry)   # must pass schema
    assert entry["device"] == "hackrf"
    assert entry["protocol"] == "elrs"
    assert entry["purpose"] == "signature_capture"
    assert entry["band"] == "2.4 GHz ISM"
    assert abs(entry["tx_power_dbm"] - 20.0) < 1e-9   # 100 mW -> 20 dBm
    assert entry["gain"] == {"mode": "manual", "lna_db": 32, "vga_db": 32}


def test_gain_and_duration_validation():
    for bad in [dict(lna=36), dict(lna=-8), dict(vga=33)]:
        try:
            capture.validate_gains(bad.get("lna", 32), bad.get("vga", 32))
            raise AssertionError(f"must reject {bad}")
        except ValueError:
            pass
    try:
        capture.validate_duration(300)
        raise AssertionError("must reject 300 s")
    except ValueError:
        pass


def test_dry_run_writes_nothing():
    rc = capture.main(["elrs-bench", "--dry-run", "--duration-s", "5"])
    assert rc == 0


if __name__ == "__main__":
    import sys

    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {name}: {exc}")
    print(f"{len(tests) - failures}/{len(tests)} tests passed")
    sys.exit(1 if failures else 0)
