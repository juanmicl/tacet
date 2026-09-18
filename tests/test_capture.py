"""No-hardware self-tests for the tacet capture CLI."""

import contextlib
import io
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

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
    with tempfile.TemporaryDirectory() as tmp:
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            # fake repo root with schema
            (Path(tmp) / "pyproject.toml").write_text("", encoding="utf-8")
            (Path(tmp) / "manifest.schema.json").write_text(
                _repo_schema(), encoding="utf-8")
            before = sorted(str(p) for p in Path(tmp).rglob("*"))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = capture.main(["elrs-bench", "--dry-run",
                                   "--duration-s", "5"])
            assert rc == 0
            after = sorted(str(p) for p in Path(tmp).rglob("*"))
            assert before == after  # dry-run created no files or dirs
        finally:
            os.chdir(old_cwd)


# --- Task 6: subprocess handling, integrity, orphan cleanup ---------------
# Erratum #5: the plan's tests use pytest monkeypatch; our runner
# (python -m tests.test_capture) has no pytest, so the scaffolding below
# reproduces it with stdlib (unittest.mock + try/finally os.chdir). All
# assertion lines from the plan are kept verbatim inside the tests.

def _repo_schema() -> str:
    """Manifest schema text from the real repo root (src-layout parents)."""
    return Path(capture.__file__).resolve().parents[3].joinpath(
        "manifest.schema.json").read_text(encoding="utf-8")


@contextlib.contextmanager
def _fake_repo_ctx(fail=False, size_bytes=None):
    """Fake repo root (chdir'd) with fake shutil.which + subprocess.run.

    Maps the plan's ``_make_fake_ctx(monkeypatch, tmpdir, ...)`` +
    ``monkeypatch.chdir(tmp)`` + fake-root writes onto stdlib mocks.
    """
    old_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            (Path(tmp) / "pyproject.toml").write_text("", encoding="utf-8")
            (Path(tmp) / "manifest.schema.json").write_text(
                _repo_schema(), encoding="utf-8")

            def fake_run(argv, **kw):
                path = argv[argv.index("-r") + 1]
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                if fail:
                    Path(path).write_bytes(b"PARTIAL")  # partial garbage
                    class R:  # fake failed result
                        returncode = 1
                    return R()
                n = int(argv[argv.index("-n") + 1])
                Path(path).write_bytes(
                    b"\0" * (size_bytes if size_bytes is not None
                             else 2 * n))
                class R:  # fake success
                    returncode = 0
                return R()

            with mock.patch.object(capture.shutil, "which",
                                   lambda name: "/usr/bin/true"), \
                 mock.patch.object(capture.subprocess, "run", fake_run):
                yield tmp
        finally:
            os.chdir(old_cwd)


def test_capture_success_writes_manifest():
    with _fake_repo_ctx():
        rc = capture.main(["elrs-bench", "--duration-s", "1",
                           "--power", "100", "--notes", "t"])
        assert rc == 0
        entries = json.loads(Path("manifest.json").read_text(encoding="utf-8"))
        assert len(entries) == 1
        assert entries[0]["protocol"] == "elrs"
        files = list(Path("data/signature_capture").rglob("*.cs8"))
        assert len(files) == 1
        assert list(Path("data/signature_capture").rglob("*.log"))  # sidecar


def test_capture_failure_cleans_up_and_writes_nothing():
    with _fake_repo_ctx(fail=True):
        rc = capture.main(["elrs-bench", "--duration-s", "1"])
        assert rc != 0
        assert not Path("manifest.json").exists()
        assert list(Path("data").rglob("*")) == []  # no orphan partial file


def test_byte_count_mismatch_recorded():
    with _fake_repo_ctx(size_bytes=123):  # truncated file
        rc = capture.main(["elrs-bench", "--duration-s", "1"])
        assert rc == 0
        entries = json.loads(Path("manifest.json").read_text(encoding="utf-8"))
        assert "byte-count" in entries[0]["operator_notes"]


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
