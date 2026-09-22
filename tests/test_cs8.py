"""Tests for the cs8 (int8 interleaved IQ) loader."""

import tempfile
from pathlib import Path

import numpy as np


def _write_cs8(directory, name, z):
    """Write complex64 z (int8-range values) as interleaved int8."""
    raw = np.empty((len(z), 2), dtype=np.int8)
    raw[:, 0] = z.real.astype(np.int8)
    raw[:, 1] = z.imag.astype(np.int8)
    path = Path(directory) / name
    raw.tofile(path)
    return str(path)


def _int8_signal(rng, n):
    """Synthetic complex64 signal with exactly representable int8 values."""
    r = rng.integers(-128, 128, size=n).astype(np.int8)
    i = rng.integers(-128, 128, size=n).astype(np.int8)
    return (r + 1j * i).astype(np.complex64)


def test_load_roundtrip():
    from tacet.loaders import cs8

    rng = np.random.default_rng(0)
    z = _int8_signal(rng, 64)
    with tempfile.TemporaryDirectory() as td:
        path = _write_cs8(td, "x.cs8", z)
        out = cs8.load_cs8(path)
    assert out.dtype == np.complex64, f"dtype {out.dtype}"
    assert out.shape == (64,), f"shape {out.shape}"
    assert np.array_equal(out, z), "roundtrip mismatch"


def test_iter_blocks_sizes():
    from tacet.loaders import cs8

    rng = np.random.default_rng(2)
    z = _int8_signal(rng, 25)
    with tempfile.TemporaryDirectory() as td:
        path = _write_cs8(td, "x.cs8", z)
        blocks = list(cs8.iter_cs8_blocks(path, 10))
    assert [len(b) for b in blocks] == [10, 10, 5], "block sizes"
    assert all(b.dtype == np.complex64 for b in blocks)
    assert np.array_equal(np.concatenate(blocks), z), "iterator roundtrip"


def test_iter_blocks_rejects_nonpositive_eagerly():
    from tacet.loaders import cs8

    # Must raise at CALL time, not on first next() (eager validation).
    try:
        cs8.iter_cs8_blocks("whatever.cs8", 0)
    except ValueError:
        return
    raise AssertionError("expected ValueError for block_samples=0")


def test_load_rejects_negative_n_samples():
    from tacet.loaders import cs8

    rng = np.random.default_rng(4)
    z = _int8_signal(rng, 8)
    with tempfile.TemporaryDirectory() as td:
        path = _write_cs8(td, "x.cs8", z)
        try:
            cs8.load_cs8(path, n_samples=-1)
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
