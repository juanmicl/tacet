"""No-hardware self-tests for the tacet capture CLI."""

import contextlib
import io
import json
import os
import tempfile
from datetime import datetime as _real_datetime
from datetime import timedelta
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
                distance_m=1.0, environment="indoor_bench", los=True,
                label="elrs", condition="bench",
                antenna="dual-band 2.4/5.8 SMA",
                path="data/signature_capture/s/elrs-bench_000.cs8",
                sha256="ab" * 32, timestamp="2026-09-19T10:00:00+00:00")
    entry = capture.build_entry(args)
    from tacet.loaders import manifest as mm
    mm.validate_entry(entry)   # must pass v2 schema
    assert entry["schema_version"] == "2.0"
    assert entry["device"] == "hackrf"
    assert entry["protocol"] == "elrs"
    assert entry["purpose"] == "signature_capture"
    assert entry["band"] == "2.4 GHz ISM"     # derived from --freq-mhz
    assert entry["label"] == "elrs"
    assert entry["condition"] == "bench"
    assert entry["environment"] == "indoor_bench"
    assert entry["los"] is True
    assert entry["distance_m"] == 1.0
    assert entry["contributor"] == "juanmicl"
    assert entry["source"] == "own_capture"
    assert entry["consent"] == "private"
    assert entry["site_anonymized"] is True
    assert abs(entry["tx_power_dbm"] - 20.0) < 1e-9   # 100 mW -> 20 dBm
    assert entry["gain"] == {"mode": "manual", "lna_db": 32, "vga_db": 32}
    assert "elrs_profile" not in entry   # optional: omitted when unknown
    assert "los_nlos" not in entry       # v1 field dropped in v2


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
                                   "--duration-s", "5",
                                   "--label", "elrs",
                                   "--condition", "bench"])
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
def _fake_repo_ctx(fail=False, size_bytes=None, interrupt=False):
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
                if interrupt:
                    Path(path).write_bytes(b"PARTIAL")  # partial garbage
                    raise KeyboardInterrupt
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
    with _fake_repo_ctx(), contextlib.redirect_stdout(io.StringIO()):
        rc = capture.main(["elrs-bench", "--duration-s", "1",
                           "--power", "100", "--notes", "t",
                           "--label", "elrs", "--condition", "bench",
                           "--distance-m", "1", "--los"])
        assert rc == 0
        doc = json.loads(Path("manifest.json").read_text(encoding="utf-8"))
        entries = doc["recordings"]
        assert len(entries) == 1
        assert entries[0]["protocol"] == "elrs"
        files = list(Path("data/signature_capture").rglob("*.cs8"))
        assert len(files) == 1
        assert list(Path("data/signature_capture").rglob("*.log"))  # sidecar


def test_capture_failure_cleans_up_and_writes_nothing():
    with _fake_repo_ctx(fail=True):
        rc = capture.main(["elrs-bench", "--duration-s", "1",
                           "--label", "elrs", "--condition", "bench"])
        assert rc != 0
        assert not Path("manifest.json").exists()
        assert list(Path("data").rglob("*")) == []  # no orphan partial file


def test_byte_count_mismatch_recorded():
    with _fake_repo_ctx(size_bytes=123), \
         contextlib.redirect_stdout(io.StringIO()), \
         contextlib.redirect_stderr(io.StringIO()):  # captured, not printed
        rc = capture.main(["elrs-bench", "--duration-s", "1",
                           "--label", "elrs", "--condition", "bench"])
        assert rc == 0
        doc = json.loads(Path("manifest.json").read_text(encoding="utf-8"))
        entries = doc["recordings"]
        assert "byte-count" in entries[0]["operator_notes"]


def test_keyboard_interrupt_cleans_up():
    with _fake_repo_ctx(interrupt=True), \
         contextlib.redirect_stdout(io.StringIO()):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = capture.main(["elrs-bench", "--duration-s", "1",
                           "--label", "elrs", "--condition", "bench"])
        assert rc == 130
        assert list(Path("data").rglob("*")) == []  # no orphan partial file
        assert not Path("manifest.json").exists()
        assert "interrupted" in err.getvalue()


def test_control_scenario_tx_off():
    args = dict(scenario="control", freq_mhz=2440, duration_s=5.0,
                lna=32, vga=32, power_mw=None, notes="tx-off control",
                distance_m=None, environment="indoor_bench", los=None,
                label="background", condition="tx_off",
                antenna="dual-band 2.4/5.8 SMA",
                path="data/signature_capture/control/control_000.cs8",
                sha256="ab" * 32, timestamp="2026-09-19T10:00:00+00:00")
    entry = capture.build_entry(args)
    from tacet.loaders import manifest as mm
    mm.validate_entry(entry)   # must pass v2 schema
    assert entry["protocol"] == "noise"
    # v2 fixes the band overloading: ALWAYS the physical band.
    assert entry["band"] == "2.4 GHz ISM"
    assert entry["label"] == "background"
    assert entry["condition"] == "tx_off"
    # --power is rejected on the control scenario (no transmitter involved).
    err = io.StringIO()
    with contextlib.redirect_stderr(err), \
         contextlib.redirect_stdout(io.StringIO()):
        rc = capture.main(["control", "--freq-mhz", "2440",
                           "--label", "background",
                           "--condition", "tx_off", "--power", "100"])
    assert rc == 2
    assert "--power" in err.getvalue()


def _shifted_clock(seconds):
    """Drop-in datetime class whose now() is shifted by `seconds`."""
    class _Shifted(_real_datetime):
        @classmethod
        def now(cls, tz=None):
            return _real_datetime.now(tz) + timedelta(seconds=seconds)
    return _Shifted


def test_two_sessions_no_id_collision():
    # C1: two bench sessions of the same scenario must both append (the
    # session-scoped id prevents the duplicate-id crash and orphans).
    with _fake_repo_ctx(), \
         contextlib.redirect_stdout(io.StringIO()), \
         contextlib.redirect_stderr(io.StringIO()):
        with mock.patch.object(capture, "datetime", _shifted_clock(0)):
            assert capture.main(["elrs-bench", "--duration-s", "1",
                                 "--label", "elrs", "--condition", "bench"]) == 0
        with mock.patch.object(capture, "datetime", _shifted_clock(3600)):
            assert capture.main(["elrs-bench", "--duration-s", "1",
                                 "--label", "elrs",
                                 "--condition", "bench"]) == 0
        doc = json.loads(Path("manifest.json").read_text(encoding="utf-8"))
        entries = doc["recordings"]
        assert len(entries) == 2
        ids = [e["id"] for e in entries]
        assert len(set(ids)) == 2                 # no id collision
        assert all(i.endswith("_elrs-bench_000") for i in ids)
        files = sorted(Path("data/signature_capture").rglob("*.cs8"))
        assert len(files) == 2                    # both captures kept
        # No orphans: every capture maps to exactly one manifest entry.
        assert ({e["channels"][0]["path"] for e in entries}
                == {str(f) for f in files})
        for f in files:                           # id names its session
            assert f"{f.parent.name}_elrs-bench_000" in ids


def test_o4_scan_picks_winner_and_cleans_failed_bin():
    # I4: with 2 bins (first fails, second has signal) the scan must pick
    # the healthy bin and leave no dwell artifacts behind.
    bins = [5170, 5800]

    def selective_run(argv, **kw):
        path = Path(argv[argv.index("-r") + 1])
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.name.startswith("scan_5170"):
            path.write_bytes(b"PARTIAL")
            class R:  # fake failed result
                returncode = 1
            return R()
        n = int(argv[argv.index("-n") + 1])
        path.write_bytes(b"\0" * (2 * n))
        class R:  # fake success
            returncode = 0
        return R()

    with _fake_repo_ctx(), contextlib.redirect_stdout(io.StringIO()):
        with mock.patch.object(capture, "o4_scan_bins", lambda: bins), \
             mock.patch.object(capture.subprocess, "run", selective_run):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = capture.main(["o4-scan", "--duration-s", "1",
                                "--label", "dji_o4", "--condition", "bench"])
        assert rc == 0
        doc = json.loads(Path("manifest.json").read_text(encoding="utf-8"))
        entries = doc["recordings"]
        assert len(entries) == 1
        assert entries[0]["center_freq_hz"] == 5.8e9   # winner, not failed bin
        assert not list(Path("data").rglob("scan_*"))  # zero dwell artifacts


def test_band_for_freq_mhz():
    f = capture.band_for_freq_mhz
    assert f(2440) == "2.4 GHz ISM"
    assert f(2400) == "2.4 GHz ISM" and f(2500) == "2.4 GHz ISM"
    assert f(5180) == "5.1 GHz EU" and f(5250) == "5.1 GHz EU"
    assert f(5800) == "5.8 GHz ISM" and f(5900) == "5.8 GHz ISM"
    assert f(650) == "UHF DVB-T"
    try:
        f(915)  # ISM 868 is not a bench band in this project (yet)
        raise AssertionError("expected ValueError for unmapped band")
    except ValueError:
        pass


def test_build_entry_v2_fields():
    args = dict(scenario="control", freq_mhz=2440, duration_s=2.0,
                lna=16, vga=16, power_mw=None, notes="",
                label="background", condition="tx_off",
                antenna="dual-band 2.4/5.8 SMA",
                path="data/signature_capture/s/control_000.cs8",
                sha256="ab" * 32, timestamp="2026-09-19T10:00:00+00:00")
    entry = capture.build_entry(args)
    from tacet.loaders import manifest as mm
    mm.validate_entry(entry)
    assert entry["schema_version"] == "2.0"
    assert entry["band"] == "2.4 GHz ISM"   # freq-derived, never "control"
    assert entry["distance_m"] is None      # required-nullable: explicit null
    assert entry["los"] is None             # required-nullable: explicit null
    assert entry["environment"] == "indoor_bench"   # CLI default
    assert "elrs_profile" not in entry      # optional: omitted when None
    assert "tx_power_dbm" not in entry
    assert "los_nlos" not in entry
    elrs = capture.build_entry({**args, "elrs_profile": "D500",
                                "power_mw": 100.0})
    assert elrs["elrs_profile"] == "D500"
    assert abs(elrs["tx_power_dbm"] - 20.0) < 1e-9


def test_elrs_profile_warning():
    # ELRS capture without --elrs-profile: one-line reminder on stderr,
    # capture itself still succeeds.
    with _fake_repo_ctx(), \
         contextlib.redirect_stdout(io.StringIO()):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = capture.main(["elrs-bench", "--duration-s", "1",
                               "--label", "elrs", "--condition", "bench"])
        assert rc == 0
        assert "--elrs-profile" in err.getvalue()


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
