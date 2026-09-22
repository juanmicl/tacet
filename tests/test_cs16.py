"""Tests for the cs16 (int16 interleaved IQ) loader."""

import tempfile
from pathlib import Path

import numpy as np


def _write_cs16(directory, name, z):
    """Write complex64 z (int16-range values) as interleaved int16."""
    raw = np.empty((len(z), 2), dtype=np.int16)
    raw[:, 0] = z.real.astype(np.int16)
    raw[:, 1] = z.imag.astype(np.int16)
    path = Path(directory) / name
    raw.tofile(path)
    return str(path)


def _int16_signal(rng, n):
    """Synthetic complex64 signal with exactly representable int16 values."""
    r = rng.integers(-32768, 32768, size=n).astype(np.int16)
    i = rng.integers(-32768, 32768, size=n).astype(np.int16)
    return (r + 1j * i).astype(np.complex64)


def test_load_roundtrip():
    from tacet.loaders import cs16

    rng = np.random.default_rng(0)
    z = _int16_signal(rng, 64)
    with tempfile.TemporaryDirectory() as td:
        path = _write_cs16(td, "x.cs16", z)
        out = cs16.load_cs16(path)
    assert out.dtype == np.complex64, f"dtype {out.dtype}"
    assert out.shape == (64,), f"shape {out.shape}"
    assert np.array_equal(out, z), "roundtrip mismatch"


def test_iter_blocks_sizes():
    from tacet.loaders import cs16

    rng = np.random.default_rng(2)
    z = _int16_signal(rng, 25)
    with tempfile.TemporaryDirectory() as td:
        path = _write_cs16(td, "x.cs16", z)
        blocks = list(cs16.iter_cs16_blocks(path, 10))
    assert [len(b) for b in blocks] == [10, 10, 5], "block sizes"
    assert all(b.dtype == np.complex64 for b in blocks)
    assert np.array_equal(np.concatenate(blocks), z), "iterator roundtrip"


def test_iter_blocks_rejects_nonpositive_eagerly():
    from tacet.loaders import cs16

    # Must raise at CALL time, not on first next() (eager validation).
    try:
        cs16.iter_cs16_blocks("whatever.cs16", 0)
    except ValueError:
        return
    raise AssertionError("expected ValueError for block_samples=0")


def test_load_rejects_negative_n_samples():
    from tacet.loaders import cs16

    rng = np.random.default_rng(4)
    z = _int16_signal(rng, 8)
    with tempfile.TemporaryDirectory() as td:
        path = _write_cs16(td, "x.cs16", z)
        try:
            cs16.load_cs16(path, n_samples=-1)
        except ValueError as exc:
            assert "n_samples" in str(exc), f"wrong error: {exc}"
            return
    raise AssertionError("expected ValueError for negative n_samples")


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
