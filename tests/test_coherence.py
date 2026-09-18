"""Synthetic self-tests for umbra.dsp.coherence and umbra.loaders.

Every test is synthetic: no hardware, no pyadi-iio. Run with either
``uv run python -m tests.test_coherence`` or ``uv run pytest tests/``.
"""

import json
import os
import tempfile

import numpy as np

from umbra.dsp import coherence
from umbra.loaders import cs16
from umbra.loaders import manifest as manifest_mod


def test_phase_offset_and_drift_recovery():
    fs = 100_000.0
    duration_s = 60.0
    f0 = 10_000.0
    z1, z2 = coherence.synthetic_tone_pair(
        fs=fs,
        duration_s=duration_s,
        f0=f0,
        phase_offset_deg=37.0,
        drift_deg_per_min=5.0,
        noise_sigma=1e-3,
    )
    b1 = coherence.downconvert_decimate(z1, fs, f_offset=f0)
    b2 = coherence.downconvert_decimate(z2, fs, f_offset=f0)
    # downconvert_decimate decimates by the integer D = round(fs / 5000 Hz).
    fs_phi = fs / int(round(fs / 5000.0))
    phi12 = coherence.phase_difference(b1, b2)
    mean_deg = np.rad2deg(np.mean(phi12))
    assert abs(mean_deg - 37.0) < 0.5, f"mean phi12 {mean_deg:.3f} deg != 37"
    slope_deg_per_min, _ = coherence.drift_rate(phi12, fs_phi)
    assert abs(slope_deg_per_min - 5.0) < 0.2, (
        f"drift {slope_deg_per_min:.3f} deg/min != 5"
    )


def test_integer_sample_offset():
    # Delay estimation needs signal bandwidth (pure tones give a flat
    # correlation), so use bandlimited noise as the test signal.
    rng = np.random.default_rng(7)
    fs = 100_000.0
    n = 200_000
    v = rng.standard_normal(n) + 1j * rng.standard_normal(n)
    b = coherence.signal.firwin(129, 0.4, window="hamming")
    v = coherence.signal.lfilter(b, 1.0, v)[128:].astype(np.complex64)
    lag_true = 7
    x1 = v[lag_true:]  # x1[i] = v[i + lag]
    x2 = v + 0.01 * (
        rng.standard_normal(v.size) + 1j * rng.standard_normal(v.size)
    ).astype(np.complex64)  # x2[i] = v[i] -> x2 lags x1 by +lag
    lag = coherence.integer_sample_offset(x1, x2)
    assert lag == lag_true, f"expected lag {lag_true}, got {lag}"
    assert coherence.integer_sample_offset(v, v.copy()) == 0


def test_downconvert_blockwise_consistency():
    fs = 100_000.0
    n = 100_000
    t = np.arange(n) / fs
    z = np.exp(1j * 2 * np.pi * 5_000.0 * t).astype(np.complex64)
    full = coherence.downconvert_decimate(z, fs, f_offset=5_000.0)
    blocked = coherence.downconvert_decimate(z, fs, f_offset=5_000.0, block_samples=8193)
    assert full.shape == blocked.shape, f"shape mismatch {full.shape} vs {blocked.shape}"
    rms = np.sqrt(np.mean(np.abs(full) ** 2))
    max_err = np.max(np.abs(full - blocked))
    assert max_err < 1e-3 * rms, f"blockwise error {max_err:.3e} vs rms {rms:.3e}"


def test_allan_deviation_white_phase_noise():
    rng = np.random.default_rng(1)
    fs_phi = 5_000.0
    phi = 0.1 * rng.standard_normal(100_000)
    taus, adev = coherence.allan_deviation(phi, fs_phi)
    taus = np.asarray(taus)
    adev = np.asarray(adev)
    mask = (taus >= 0.1) & (taus <= 1.0)
    assert mask.sum() >= 5, "not enough points in the fitted decade"
    slope = np.polyfit(np.log10(taus[mask]), np.log10(adev[mask]), 1)[0]
    assert abs(slope - (-1.0)) < 0.1, f"allan slope {slope:.3f} != -1 (white PM)"


def test_window_phase_stats_sanity():
    rng = np.random.default_rng(2)
    fs_phi = 5_000.0
    # White phase noise: within-window std stays ~sigma for every window
    # length (only the std of window MEANS would shrink with length).
    phi_white = np.deg2rad(2.0) * rng.standard_normal(50_000)
    stats = coherence.window_phase_stats(phi_white, fs_phi, windows_s=(0.01, 0.1, 1.0))
    for w, s in stats.items():
        assert s["n_windows"] > 0
        assert np.isfinite(s["mean_deg"]) and np.isfinite(s["std_deg"])
        assert 0.5 * 2.0 < s["std_deg"] < 1.5 * 2.0, (w, s)
    # With drift, long windows must see more spread than short ones.
    t = np.arange(50_000) / fs_phi
    phi_drift = np.deg2rad(60.0) * (t / 60.0) + np.deg2rad(0.05) * rng.standard_normal(
        50_000
    )
    stats_d = coherence.window_phase_stats(phi_drift, fs_phi, windows_s=(0.01, 1.0))
    assert stats_d[1.0]["std_deg"] > 5 * stats_d[0.01]["std_deg"], stats_d


def test_iq_imbalance_recovery():
    z1, z2 = coherence.synthetic_tone_pair(
        fs=100_000.0,
        duration_s=0.5,
        f0=10_000.0,
        phase_offset_deg=0.0,
        noise_sigma=0.0,
        amp_imbalance_db=1.0,
        phase_skew_deg=2.0,
    )
    m = coherence.iq_imbalance_tone(z2)
    assert abs(m["amp_imbalance_db"] - 1.0) < 0.05, m
    assert abs(m["phase_skew_deg"] - 2.0) < 0.1, m


def test_manifest_roundtrip():
    entry = {
        "id": "coh-0001",
        "timestamp": "2026-09-18T20:00:00+02:00",
        "device": "pluto_plus",
        "protocol": "fm_carrier",
        "purpose": "coherence_cal",
        "center_freq_hz": 98_500_000.0,
        "sample_rate_sps": 2_560_000.0,
        "format": "cs16",
        "duration_s": 60.0,
        "channels": [
            {"path": "data/coherence_cal/s1/chA_000.cs16", "sha256": "00" * 32},
            {"path": "data/coherence_cal/s1/chB_000.cs16", "sha256": "11" * 32},
        ],
        "antenna": "ANT500 telescopic",
        "operator_notes": "self-test",
        "gain": {"mode": "manual", "rx1_db": 30.0, "rx2_db": 30.0},
        "rf_chain": {"splitter": "2-way 50R SMA", "cable_len_m": 1.0},
    }
    with tempfile.TemporaryDirectory() as tmp:
        mpath = os.path.join(tmp, "manifest.json")
        manifest_mod.validate_entry(entry)
        manifest_mod.append_entry(entry, manifest_path=mpath)
        rows = manifest_mod.query(manifest_path=mpath, purpose="coherence_cal")
        assert len(rows) == 1 and rows[0]["id"] == "coh-0001"
        assert manifest_mod.query(manifest_path=mpath, device="hackrf") == []
        with open(mpath) as fh:
            assert json.load(fh)[0]["device"] == "pluto_plus"

        bad = dict(entry)
        del bad["id"]
        try:
            manifest_mod.validate_entry(bad)
            raise AssertionError("missing required field must raise")
        except ValueError:
            pass

        bad = dict(entry)
        bad["device"] = "pluto"
        try:
            manifest_mod.validate_entry(bad)
            raise AssertionError("bad enum must raise")
        except ValueError:
            pass


def test_cs16_loader_roundtrip():
    rng = np.random.default_rng(3)
    ref = (rng.integers(-3000, 3000, 1024) + 1j * rng.integers(-3000, 3000, 1024))
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "x.cs16")
        np.asarray([ref.real, ref.imag]).T.astype("<i2").tofile(path)
        z = cs16.load_cs16(path)
        assert z.dtype == np.complex64
        assert np.array_equal(z, ref.astype(np.complex64))
        part = cs16.load_cs16(path, n_samples=100)
        assert part.shape == (100,)
        blocks = list(cs16.iter_cs16_blocks(path, 256))
        assert len(blocks) == 4 and all(b.shape == (256,) for b in blocks)
        assert np.array_equal(np.concatenate(blocks), z)


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
        except Exception as exc:  # noqa: BLE001 - report and continue
            failures += 1
            print(f"FAIL {name}: {exc}")
    print(f"{len(tests) - failures}/{len(tests)} tests passed")
    sys.exit(1 if failures else 0)
