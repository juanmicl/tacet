"""Synthetic self-tests for cs8 loader and tacet.dsp.features."""

import os
import tempfile
from pathlib import Path

import numpy as np

from tacet.loaders import cs8


def test_cs8_loader_roundtrip():
    rng = np.random.default_rng(3)
    ref = (rng.integers(-120, 120, 1024) + 1j * rng.integers(-120, 120, 1024))
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "x.cs8")
        np.asarray([ref.real, ref.imag]).T.astype("<i1").tofile(path)
        z = cs8.load_cs8(path)
        assert z.dtype == np.complex64
        assert np.array_equal(z, ref.astype(np.complex64))
        part = cs8.load_cs8(path, n_samples=100)
        assert part.shape == (100,)
        blocks = list(cs8.iter_cs8_blocks(path, 256))
        assert len(blocks) == 4 and all(b.shape == (256,) for b in blocks)
        assert np.array_equal(np.concatenate(blocks), z)


def test_cs8_odd_byte_count():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "odd.cs8")
        np.asarray([1, 2, 3, 4, 5], dtype="<i1").tofile(path)  # 2.5 samples
        z = cs8.load_cs8(path)
        assert z.shape == (2,)  # trailing half-sample ignored


def test_manifest_hackrf_gain_and_repo_root():
    from tacet.loaders import manifest as mm

    entry = {
        "id": "sig-0001",
        "timestamp": "2026-09-19T10:00:00+00:00",
        "device": "hackrf",
        "protocol": "elrs",
        "purpose": "signature_capture",
        "center_freq_hz": 2_440_000_000.0,
        "sample_rate_sps": 20_000_000.0,
        "format": "cs8",
        "duration_s": 5.0,
        "channels": [{"path": "data/signature_cal/s/a.cs8", "sha256": "00" * 32}],
        "antenna": "dual-band 2.4/5.8 SMA",
        "operator_notes": "test",
        "band": "2.4 GHz ISM",
        "tx_power_dbm": 20.0,
        "gain": {"mode": "manual", "lna_db": 32, "vga_db": 32},
    }
    mm.validate_entry(entry)  # lna_db/vga_db must be accepted
    with tempfile.TemporaryDirectory() as tmp:
        # fake repo root with schema + manifest
        (Path(tmp) / "pyproject.toml").write_text("", encoding="utf-8")
        _schema = Path("manifest.schema.json").read_text(encoding="utf-8")
        (Path(tmp) / "manifest.schema.json").write_text(_schema, encoding="utf-8")
        root = mm._repo_root(start=Path(tmp) / "sub" / "dir")
        assert root == Path(tmp).resolve()  # walks up to pyproject.toml


def test_spectral_helpers_frequency_axis():
    from tacet.dsp import features

    fs = 1_000_000.0
    n = 65536
    t = np.arange(n) / fs
    tone_hz = 250_000.0  # +5 bins-scale offset inside the window
    x = np.exp(2j * np.pi * tone_hz * t).astype(np.complex64)

    f_spec, _, S = features.spectrogram(x, fs, nfft=1024)
    peak_f = f_spec[int(np.argmax(S.mean(axis=1)))]
    assert abs(peak_f - tone_hz) < 2_000.0, f"spec peak {peak_f}"

    f_w, p = features.welch_psd(x, fs, nfft=1024)
    peak_f = f_w[int(np.argmax(p))]
    assert abs(peak_f - tone_hz) < 2_000.0, f"welch peak {peak_f}"

    assert np.all(np.diff(f_spec) > 0)  # axis sorted ascending (fftshifted)


def test_remove_dc_and_time_envelope():
    from tacet.dsp import features

    fs = 1_000_000.0
    n = 65536
    t = np.arange(n) / fs
    rng = np.random.default_rng(5)
    tone = np.exp(2j * np.pi * 1e5 * t).astype(np.complex64)
    tone[n // 8:] = 0  # burst: tone only in 8 of 64 columns
    noise = 1e-3 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    x = 500.0 + tone + noise.astype(np.complex64)  # big DC + burst + floor
    y = features.remove_dc(x)
    assert abs(np.mean(y)) < 1e-3

    _, _, S = features.spectrogram(y, fs, nfft=1024)
    env = features.time_envelope(S)
    assert env.shape[0] == S.shape[1]
    # DC bins excluded -> envelope floor stays far below the tone peak
    assert env.max() > 1e3 * np.median(env)


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
