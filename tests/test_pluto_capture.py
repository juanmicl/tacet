"""No-hardware self-tests for the Pluto+ capture backend (Task 3).

The FakeAd9361 here exposes ONLY the real pyadi attribute surface and
raises on unknown attribute names: that is the guard against the
notebook-02 silent-no-op bug class, where a typo'd property write on the
real AD9361 object was silently ignored and the configuration never
reached the hardware. Seams under test: ``pluto._set_device_factory``
and ``pluto._clock`` (both reset in try/finally).
"""

import contextlib
import io
import json
import math
import os
import sys
import tempfile
import time
from datetime import datetime as _real_datetime
from datetime import timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from tacet.loaders import manifest as manifest_mod
from tacet.nodes import capture
from tacet.nodes.backends import pluto

DUAL_RATE = 2_560_000          # coherence work (notebook-02 layout)
MONO_RATE = 20_000_000         # widest single-RX bench rate
BUF_DUAL = 2_560_000           # ~1 s per rx() at 2.56 Msps
BUF_MONO = 2 ** 20             # ~52 ms per rx() at 20 Msps
TX_MUTE_DB = -89.75


# --- The restricted fake -----------------------------------------------------


class FakeAd9361:
    """pyadi AD9361 fake exposing ONLY the real attribute surface.

    ``__setattr__`` records every accepted write (in order) and RAISES
    on any attribute name the real object does not have — the direct
    guard against the notebook-02 silent-no-op bug class. ``rx()``
    returns whatever the scripted ``rx_fn(sdr)`` produces (single array
    for mono, ``[chA, chB]`` for dual).
    """

    REAL_ATTRS = frozenset({
        "gain_control_mode_chan0", "gain_control_mode_chan1",
        "rx_hardwaregain_chan0", "rx_hardwaregain_chan1",
        "tx_hardwaregain_chan0", "tx_hardwaregain_chan1",
        "rx_enabled_channels", "sample_rate", "rx_rf_bandwidth",
        "rx_lo", "rx_buffer_size",
    })

    DEFAULTS = {
        "gain_control_mode_chan0": "slow_attack",
        "gain_control_mode_chan1": "slow_attack",
        "rx_hardwaregain_chan0": 0.0,
        "rx_hardwaregain_chan1": 0.0,
        "tx_hardwaregain_chan0": 0.0,
        "tx_hardwaregain_chan1": 0.0,
        "rx_enabled_channels": [],
        "sample_rate": 30_720_000,
        "rx_rf_bandwidth": 4_000_000,
        "rx_lo": 2_400_000_000,
        "rx_buffer_size": 1024,
    }

    def __init__(self, rx_fn=None):
        object.__setattr__(self, "_writes", [])
        object.__setattr__(self, "_rx_calls", 0)
        object.__setattr__(self, "_rx_fn", rx_fn)
        object.__setattr__(self, "_rxadc", _FakeRxAdc(self._writes))
        for name, value in self.DEFAULTS.items():
            object.__setattr__(self, name, value)

    def __setattr__(self, name, value):
        if name not in self.REAL_ATTRS:
            raise AttributeError(
                f"FakeAd9361 has no pyadi attribute {name!r} "
                "(notebook-02 silent-no-op guard)")
        object.__setattr__(self, name, value)
        self._writes.append((name, value))

    def rx(self):
        object.__setattr__(self, "_rx_calls", self._rx_calls + 1)
        if self._rx_fn is None:
            raise RuntimeError("no rx() behaviour scripted")
        return self._rx_fn(self)


class _FakeRxAdc:
    """Stand-in for the real ``sdr._rxadc`` kernel-buffer tweak target."""

    def __init__(self, writes):
        self._writes = writes

    def set_kernel_buffers_count(self, n):
        self._writes.append(("_rxadc.set_kernel_buffers_count", n))


def dual_rx_fn(buf=BUF_DUAL, second=None, fail_on_call=None, fail_exc=None):
    """Script dual ``rx()``: [chA, chB] arrays; optional failure injection."""
    def rx_fn(sdr):
        if fail_on_call is not None and sdr._rx_calls == fail_on_call:
            raise fail_exc
        z = np.zeros(buf, dtype=np.complex64)
        return [z, z.copy() if second is None else second]
    return rx_fn


def mono_rx_fn(buf=BUF_MONO, short=None, fail_on_call=None, fail_exc=None):
    """Script mono ``rx()``: one array; optional short reads / failures."""
    n = short if short is not None else buf
    def rx_fn(sdr):
        if fail_on_call is not None and sdr._rx_calls == fail_on_call:
            raise fail_exc
        return np.zeros(n, dtype=np.complex64)
    return rx_fn


# --- Seams and scaffolding ---------------------------------------------------


def scripted_clock(values):
    """Clock seam returning the scripted values in order."""
    it = iter(values)
    return lambda: next(it)


def window_clock(windows, t0=0.0):
    """Clock script emitting two calls per timed rx(): (before, after)."""
    vals = []
    t = t0
    for w in windows:
        vals.extend([t, t + w])
        t += w
    return scripted_clock(vals)


def _repo_schema() -> str:
    """Manifest schema text from the real repo root (src-layout parents)."""
    return Path(capture.__file__).resolve().parents[3].joinpath(
        "manifest.schema.json").read_text(encoding="utf-8")


def _fixed_datetime(stamp):
    """Drop-in datetime class whose now() is pinned to a UTC stamp."""
    dt = _real_datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(
        tzinfo=timezone.utc)
    class _Fixed(_real_datetime):
        @classmethod
        def now(cls, tz=None):
            return dt if tz is None else dt.astimezone(tz)
    return _Fixed


@contextlib.contextmanager
def _pluto_ctx(rx_fn=None, clock=None, stamp=None):
    """Fake repo root + pluto seams (factory, clock, check_available).

    Mirrors test_capture._fake_repo_ctx for the pluto backend; every seam
    is reset in the finally block.
    """
    old_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            (Path(tmp) / "pyproject.toml").write_text("", encoding="utf-8")
            (Path(tmp) / "manifest.schema.json").write_text(
                _repo_schema(), encoding="utf-8")
            made = []

            def factory(uri):
                sdr = FakeAd9361(rx_fn=rx_fn)
                made.append(sdr)
                return sdr

            pluto._set_device_factory(factory)
            saved_clock = pluto._clock
            if clock is not None:
                pluto._clock = clock
            saved_dt = capture.datetime
            if stamp is not None:
                capture.datetime = _fixed_datetime(stamp)
            with mock.patch.object(pluto, "check_available", lambda: None):
                yield SimpleNamespace(tmp=Path(tmp), made=made)
        finally:
            pluto._set_device_factory(None)
            pluto._clock = saved_clock
            capture.datetime = saved_dt
            os.chdir(old_cwd)


COHERENCE_ARGS = ["coherence-cal", "--device", "pluto", "--gain-db", "40",
                  "--label", "interference", "--condition", "bench"]


def _run_main(argv):
    """Run capture.main with stdio captured; return (rc, out, err)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = capture.main(argv)
    return rc, out.getvalue(), err.getvalue()


def _hostile_channel():
    """A channel buffer that deterministically fails the cs16 conversion."""
    return np.array([None], dtype=object)


# --- 1. The notebook-02 guard ------------------------------------------------


def test_fake_rejects_unknown_attributes():
    sdr = FakeAd9361()
    try:
        sdr.does_not_exist = 1
        raise AssertionError("unknown attribute write must raise")
    except AttributeError as exc:
        assert "does_not_exist" in str(exc)


# --- 2. Pinned pyadi write order ----------------------------------------------


def test_open_pluto_write_order():
    pluto._set_device_factory(lambda uri: FakeAd9361())
    try:
        sdr = pluto.open_pluto("ip:fake", 98.5e6, 40.0, DUAL_RATE, dual=True)
        writes = list(sdr._writes)
        # TX muted FIRST, both channels, at -89.75 (receive-only invariant).
        assert writes[0] == ("tx_hardwaregain_chan0", TX_MUTE_DB)
        assert writes[1] == ("tx_hardwaregain_chan1", TX_MUTE_DB)
        # sample_rate directly after the mutes...
        assert writes[2] == ("sample_rate", DUAL_RATE)
        names = [n for n, _ in writes]

        def idx(name):
            return names.index(name)

        # ...then the pinned global -> rx chain order.
        assert idx("rx_rf_bandwidth") < idx("rx_lo")
        assert (idx("rx_lo")
                < idx("gain_control_mode_chan0")
                < idx("rx_hardwaregain_chan0"))
        assert (idx("gain_control_mode_chan1")
                < idx("rx_hardwaregain_chan1")
                < idx("rx_enabled_channels")
                < idx("rx_buffer_size")
                < idx("_rxadc.set_kernel_buffers_count"))
        assert writes[-1][0] == "_rxadc.set_kernel_buffers_count"
        # Dual: both RX channels enabled; ~1 s per rx() at 2.56 Msps.
        assert sdr.rx_enabled_channels == [0, 1]
        assert sdr.rx_buffer_size == BUF_DUAL
        assert sdr.gain_control_mode_chan0 == "manual"
        assert sdr.rx_hardwaregain_chan0 == 40.0
        # Never anything TX beyond the two mutes (no tx buffers, no tx()).
        tx_writes = [n for n, _ in writes if "tx" in n]
        assert tx_writes == ["tx_hardwaregain_chan0", "tx_hardwaregain_chan1"]

        mono = pluto.open_pluto("ip:fake", 5.8e9, 30.0, MONO_RATE, dual=False)
        assert mono.rx_enabled_channels == [0]
        assert mono.rx_buffer_size == BUF_MONO
    finally:
        pluto._set_device_factory(None)


# --- 3. Validator envelope ----------------------------------------------------


def _ns(freq_mhz, duration_s, gain_db, scenario="o4-capture"):
    return SimpleNamespace(freq_mhz=freq_mhz, duration_s=duration_s,
                           gain_db=gain_db, lna=32, vga=32,
                           scenario=scenario, scan_fm=False)


def test_validate_envelope_pluto():
    pluto.validate(_ns(98.5, 5.0, 40.0))       # FM coherence carrier: pass
    pluto.validate(_ns(87.5, 5.0, 40.0))       # lower FM edge: pass
    for bad in [_ns(69.0, 5.0, 40.0), _ns(6001.0, 5.0, 40.0)]:
        try:
            pluto.validate(bad)
            raise AssertionError(f"must reject freq {bad.freq_mhz}")
        except ValueError:
            pass
    # Duration caps are per scenario: the 20 Msps mono o4-capture is RAM
    # bound (60 s), the 2.56 Msps scenarios run to 660 s.
    pluto.validate(_ns(98.5, 660.0, 40.0, scenario="coherence-cal"))
    pluto.validate(_ns(5800, 60.0, 40.0))      # o4-capture at its cap: pass
    for bad in [_ns(98.5, 661.0, 40.0, scenario="coherence-cal"),
                _ns(5800, 60.1, 40.0)]:
        try:
            pluto.validate(bad)
            raise AssertionError(f"must reject duration {bad.duration_s} "
                                 f"for {bad.scenario}")
        except ValueError:
            pass
    for bad in [_ns(98.5, 5.0, -1.0), _ns(98.5, 5.0, 74.6)]:
        try:
            pluto.validate(bad)
            raise AssertionError(f"must reject gain {bad.gain_db}")
        except ValueError:
            pass
    pluto.validate(_ns(98.5, 5.0, 0.0))        # 0 dB: pass
    pluto.validate(_ns(98.5, 5.0, 74.5))       # 74.5 dB: pass (no steps)


# --- 4. Entry schema round-trip through the REAL front-end ---------------------


def test_coherence_entry_schema_round_trip():
    ns = SimpleNamespace(
        scenario="coherence-cal", freq_mhz=98.5, duration_s=1.0,
        gain_db=40.0, uri="ip:fake", notes="", antenna="ANT500",
        label="interference", condition="bench", distance_m=None,
        environment="indoor_bench", los=None, contributor="juanmicl",
        elrs_profile=None, power=None)
    with _pluto_ctx(rx_fn=dual_rx_fn()):
        session_dir = Path("data/coherence_cal/S")
        parts = pluto.capture_once(ns, session_dir, 0)
        assert parts is not None
        entry = capture.assemble_entry(parts, ns, session_dir, 0)
        manifest_mod.validate_entry(entry)     # must pass the v2 schema
        assert entry["device"] == "pluto_plus"
        assert entry["format"] == "cs16"
        assert entry["protocol"] == "fm_carrier"
        assert entry["purpose"] == "coherence_cal"
        assert entry["band"] == "VHF FM broadcast"
        assert entry["gain"] == {"mode": "manual", "rx1_db": 40.0,
                                 "rx2_db": 40.0}
        assert len(entry["channels"]) == 2
        for ch in entry["channels"]:
            assert len(ch["sha256"]) == 64     # real sha256 of a real file
        assert "rf_chain" in entry["operator_notes"]


# --- 5. Zero-orphan: the FULL file set dies on every failure leg ---------------


def test_zero_orphan_file_set():
    # Leg (a): the second channel write raises (chA already on disk).
    with _pluto_ctx(rx_fn=dual_rx_fn(second=_hostile_channel())):
        rc, out, err = _run_main(COHERENCE_ARGS + ["--duration-s", "1"])
        assert rc == 2
        assert list(Path("data").rglob("*")) == []      # no channel files
        assert not Path("manifest.json").exists()

    # Leg (b): KeyboardInterrupt injected between buffers.
    with _pluto_ctx(rx_fn=dual_rx_fn(fail_on_call=3,
                                     fail_exc=KeyboardInterrupt())):
        rc, out, err = _run_main(COHERENCE_ARGS + ["--duration-s", "1"])
        assert rc == 130
        assert "interrupted" in err
        assert list(Path("data").rglob("*")) == []
        assert not Path("manifest.json").exists()

    # Leg (c): append_entry fails AFTER both files were written
    # (pre-seeded duplicate id): files removed, manifest byte-unchanged.
    stamp = "20261010T120000Z"
    with _pluto_ctx(rx_fn=dual_rx_fn(), stamp=stamp):
        Path("manifest.json").write_text(json.dumps({
            "schema_version": "2.0",
            "recordings": [{"id": f"{stamp}_coherence-cal_000"}],
        }), encoding="utf-8")
        before = Path("manifest.json").read_bytes()
        rc, out, err = _run_main(COHERENCE_ARGS + ["--duration-s", "1"])
        assert rc == 2
        assert "duplicate recording id" in err
        assert Path("manifest.json").read_bytes() == before
        assert list(Path("data").rglob("*")) == []      # files + sidecar gone


# --- 6/7/8. The rate guard ------------------------------------------------------


def test_rate_guard_discards_first_buffer():
    # First measured window pathologically slow, next 3 at exactly 100%:
    # the capture must PROCEED and the sidecar must record the true rate.
    fine = BUF_MONO / MONO_RATE
    ns = SimpleNamespace(scenario="o4-capture", freq_mhz=5800,
                         duration_s=0.2, gain_db=30.0, uri="ip:fake",
                         notes="")
    with _pluto_ctx(rx_fn=mono_rx_fn(),
                    clock=window_clock([50.0, fine, fine, fine])) as ctx:
        parts = pluto.capture_once(ns, Path("data/signature_capture/S"), 0)
        assert parts is not None
        assert parts["notes_warn"] == ""
        sidecar = json.loads(Path(parts["sidecar_path"]).read_text())
        assert abs(sidecar["achieved_rate_sps"] - MONO_RATE) / MONO_RATE < 0.01
        assert ctx.made[0]._rx_calls == 1 + 4    # warm-up discard + 4 reads


def test_mono_rate_shortfall_aborts_zero_orphan():
    # Consistent 50% delivery on a mono scenario: ValueError, no files.
    slow = BUF_MONO / (0.5 * MONO_RATE)
    ns = SimpleNamespace(scenario="o4-capture", freq_mhz=5800,
                         duration_s=0.2, gain_db=30.0, uri="ip:fake",
                         notes="")
    with _pluto_ctx(rx_fn=mono_rx_fn(), clock=window_clock([slow] * 4)):
        session_dir = Path("data/signature_capture/S")
        try:
            pluto.capture_once(ns, session_dir, 0)
            raise AssertionError("expected the rate guard to abort")
        except ValueError as exc:
            assert "rate guard" in str(exc)
        assert list(Path("data").rglob("*")) == []
        assert not Path("manifest.json").exists()


def test_mono_rate_shortfall_clean_error_through_main():
    # The deliberate rate-guard ValueError must surface as the house
    # `error: ...` + rc 2 through capture.main — never a traceback.
    slow = BUF_MONO / (0.5 * MONO_RATE)
    n_reads = max(4, math.ceil(MONO_RATE * 1.0 / BUF_MONO))  # duration 1 s
    with _pluto_ctx(rx_fn=mono_rx_fn(),
                    clock=window_clock([slow] * n_reads)):
        rc, out, err = _run_main(
            ["o4-capture", "--device", "pluto", "--freq-mhz", "5800",
             "--gain-db", "30", "--label", "dji_o4",
             "--condition", "bench", "--duration-s", "1"])
        assert rc == 2
        assert "error:" in err
        assert "rate guard" in err
        assert "Traceback" not in err
        assert list(Path("data").rglob("*")) == []
        assert not Path("manifest.json").exists()


def test_dual_rate_shortfall_warns_not_aborts():
    # Dual at 80%: capture completes; the sidecar records the achieved
    # rate and a rate warning lands in operator_notes.
    slow = BUF_DUAL / (0.8 * DUAL_RATE)
    with _pluto_ctx(rx_fn=dual_rx_fn(), clock=window_clock([slow] * 4)):
        rc, out, err = _run_main(COHERENCE_ARGS + ["--duration-s", "1"])
        assert rc == 0
        doc = json.loads(Path("manifest.json").read_text(encoding="utf-8"))
        entry = doc["recordings"][0]
        assert "rate warning" in entry["operator_notes"]
        assert len(entry["channels"]) == 2
        sidecar_path = Path("data/coherence_cal")
        sidecars = list(sidecar_path.rglob("*.json"))
        assert len(sidecars) == 1
        sidecar = json.loads(sidecars[0].read_text())
        assert sidecar["requested_rate_sps"] == DUAL_RATE
        assert sidecar["achieved_rate_sps"] < DUAL_RATE


# --- 9. FM scan with a leading transient per bin --------------------------------


def test_scan_fm_picks_strongest():
    # The scan range must stop short of 108 MHz (band map is exclusive
    # there): a 108.0 winner would raise inside entry_parts AFTER the
    # files were written.
    bins = pluto.fm_scan_bins()
    assert bins[0] == 88.0 and bins[-1] < 108.0
    # Scripted steady powers with the LARGEST transient parked on a
    # NON-winner bin: without the 0.1 s settle discard the transient bin
    # (98.0) flips the winner and this test FAILS; with it, the steady
    # winner (98.2) must come through.
    powers = {98.0: 4.0, 98.2: 9.0, 98.4: 1.0}      # steady winner: 98.2
    transients = {98.0: 1e6, 98.2: 1.0, 98.4: 1.0}  # peak transient: 98.0
    settle = int(0.1 * DUAL_RATE)

    def rx_fn(sdr):
        mhz = round(sdr.rx_lo / 1e6, 6)
        z = np.zeros(BUF_DUAL, dtype=np.complex64)
        z[:settle] = transients[mhz] * (1 + 1j)     # leading transient
        z[settle:] = math.sqrt(powers[mhz]) * (1 + 0j)
        return [z, z.copy()]

    with _pluto_ctx(rx_fn=rx_fn) as ctx:
        with mock.patch.object(pluto, "fm_scan_bins",
                               lambda: [98.0, 98.2, 98.4]):
            rc, out, err = _run_main(COHERENCE_ARGS + [
                "--scan-fm", "--duration-s", "1"])
        assert rc == 0
        doc = json.loads(Path("manifest.json").read_text(encoding="utf-8"))
        entry = doc["recordings"][0]
        assert entry["center_freq_hz"] == 98.2e6    # steady winner...
        assert entry["band"] == "VHF FM broadcast"
        # ...and the winner became rx_lo on the capture device (the last
        # fake the factory built is the capture-phase device).
        last = ctx.made[-1]
        lo_writes = [v for n, v in last._writes if n == "rx_lo"]
        assert lo_writes[-1] == 98_200_000
        # No scan dwell artifacts: only the two channel files + sidecar.
        files = sorted(p.name for p in Path("data").rglob("*") if p.is_file())
        assert files == ["coherence-cal_000.json",
                         "coherence-cal_000_ch1.cs16",
                         "coherence-cal_000_ch2.cs16"]


# --- 10. check_available hint ----------------------------------------------------


def test_check_available_hint():
    saved = sys.modules.get("adi")
    sys.modules["adi"] = None
    try:
        try:
            pluto.check_available()
            raise AssertionError("expected ValueError without pyadi")
        except ValueError as exc:
            assert "uv sync --extra acquisition" in str(exc)
    finally:
        if saved is None:
            sys.modules.pop("adi", None)
        else:
            sys.modules["adi"] = saved


# --- 11. Dry-run: config preview, no device, no files ----------------------------


def test_dry_run_output():
    def boom(uri):
        raise AssertionError("device must not be opened in dry-run")

    pluto._set_device_factory(boom)
    try:
        with _fake_repo_only():
            before = sorted(str(p) for p in Path(".").rglob("*"))
            rc, out, err = _run_main(
                COHERENCE_ARGS + ["--dry-run", "--duration-s", "5"])
            assert rc == 0
            after = sorted(str(p) for p in Path(".").rglob("*"))
            assert before == after                 # nothing created
            assert '"sha256": "(dry-run)"' in out
            assert "ip:192.168.2.1" in out         # config shown
            assert "coherence_cal" in out          # session root shown
            rc, out, err = _run_main(
                ["o4-capture", "--device", "pluto", "--freq-mhz", "5800",
                 "--gain-db", "30", "--label", "dji_o4", "--condition",
                 "bench", "--dry-run"])
            assert rc == 0
            assert '"sha256": "(dry-run)"' in out
    finally:
        pluto._set_device_factory(None)


@contextlib.contextmanager
def _fake_repo_only():
    """Chdir into a fake repo root (pyproject + schema), no device seams."""
    old_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            (Path(tmp) / "pyproject.toml").write_text("", encoding="utf-8")
            (Path(tmp) / "manifest.schema.json").write_text(
                _repo_schema(), encoding="utf-8")
            yield
        finally:
            os.chdir(old_cwd)


# --- 12. Dual vs mono channel counts ---------------------------------------------


def test_dual_vs_mono_channel_counts():
    dual_ns = SimpleNamespace(scenario="coherence-cal", freq_mhz=98.5,
                              duration_s=1.0, gain_db=40.0, uri="ip:fake",
                              notes="")
    with _pluto_ctx(rx_fn=dual_rx_fn()):
        parts = pluto.capture_once(dual_ns, Path("data/coherence_cal/S"), 0)
        assert len(parts["channels"]) == 2
        for ch in parts["channels"]:
            assert Path(ch["path"]).stat().st_size == DUAL_RATE * 4

    mono_ns = SimpleNamespace(scenario="o4-capture", freq_mhz=5800,
                              duration_s=0.25, gain_db=30.0, uri="ip:fake",
                              notes="")
    with _pluto_ctx(rx_fn=mono_rx_fn()):
        parts = pluto.capture_once(mono_ns, Path("data/signature_capture/S"),
                                   0)
        assert len(parts["channels"]) == 1
        assert Path(parts["channels"][0]["path"]).stat().st_size == 5_000_000 * 4


# --- 13. Byte-count warning path --------------------------------------------------


def test_byte_count_warning_path():
    # The fake delivers half-size buffers: fewer samples than requested,
    # the mismatch is noted and the capture is KEPT.
    with _pluto_ctx(rx_fn=mono_rx_fn(short=BUF_MONO // 2)):
        rc, out, err = _run_main(
            ["control", "--device", "pluto", "--freq-mhz", "2440",
             "--gain-db", "30", "--label", "background",
             "--condition", "tx_off", "--duration-s", "1"])
        assert rc == 0
        doc = json.loads(Path("manifest.json").read_text(encoding="utf-8"))
        entry = doc["recordings"][0]
        assert "byte-count" in entry["operator_notes"]
        files = list(Path("data").rglob("*.cs16"))
        assert len(files) == 1                       # capture kept


if __name__ == "__main__":
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
