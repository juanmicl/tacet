"""Tests for the cf32 (float32 interleaved IQ) loader."""

import tempfile
from pathlib import Path

import numpy as np


def _write_cf32(directory, name, z):
    """Write complex64 z as interleaved float32 and return the path."""
    raw = np.empty((len(z), 2), dtype=np.float32)
    raw[:, 0] = z.real
    raw[:, 1] = z.imag
    path = Path(directory) / name
    raw.tofile(path)
    return str(path)


def test_load_cf32_roundtrip():
    from tacet.loaders import cf32

    rng = np.random.default_rng(0)
    z = (rng.standard_normal(64) + 1j * rng.standard_normal(64)).astype(
        np.complex64
    )
    with tempfile.TemporaryDirectory() as td:
        path = _write_cf32(td, "x.cf32", z)
        out = cf32.load_cf32(path)
    assert out.dtype == np.complex64, f"dtype {out.dtype}"
    assert out.shape == (64,), f"shape {out.shape}"
    assert np.array_equal(out, z), "roundtrip mismatch"


def test_load_cf32_n_samples_clamps():
    from tacet.loaders import cf32

    rng = np.random.default_rng(3)
    z = (rng.standard_normal(64) + 1j * rng.standard_normal(64)).astype(
        np.complex64
    )
    with tempfile.TemporaryDirectory() as td:
        path = _write_cf32(td, "x.cf32", z)
        out = cf32.load_cf32(path, n_samples=1000)
    assert len(out) == 64, f"expected clamp to 64, got {len(out)}"
    assert np.array_equal(out, z)


def test_load_cf32_start_offset_and_short_read():
    from tacet.loaders import cf32

    rng = np.random.default_rng(1)
    z = (rng.standard_normal(50) + 1j * rng.standard_normal(50)).astype(
        np.complex64
    )
    with tempfile.TemporaryDirectory() as td:
        path = _write_cf32(td, "x.cf32", z)
        out = cf32.load_cf32(path, n_samples=10, start=40)
        short = cf32.load_cf32(path, n_samples=99, start=40)
    assert np.array_equal(out, z[40:50]), "offset window mismatch"
    assert np.array_equal(short, z[40:]), "short read should return available"


def test_load_cf32_rejects_negative_start():
    from tacet.loaders import cf32

    rng = np.random.default_rng(4)
    z = (rng.standard_normal(8) + 1j * rng.standard_normal(8)).astype(
        np.complex64
    )
    with tempfile.TemporaryDirectory() as td:
        path = _write_cf32(td, "x.cf32", z)
        try:
            cf32.load_cf32(path, start=-1)
        except ValueError:
            return
    raise AssertionError("expected ValueError for negative start")


def test_iter_cf32_blocks_sizes():
    from tacet.loaders import cf32

    rng = np.random.default_rng(2)
    z = (rng.standard_normal(25) + 1j * rng.standard_normal(25)).astype(
        np.complex64
    )
    with tempfile.TemporaryDirectory() as td:
        path = _write_cf32(td, "x.cf32", z)
        blocks = list(cf32.iter_cf32_blocks(path, 10))
    assert [len(b) for b in blocks] == [10, 10, 5], "block sizes"
    assert all(b.dtype == np.complex64 for b in blocks)
    assert np.array_equal(np.concatenate(blocks), z), "iterator roundtrip"


def test_iter_cf32_blocks_rejects_nonpositive():
    from tacet.loaders import cf32

    try:
        cf32.iter_cf32_blocks("whatever.dat", 0)
    except ValueError:
        return
    raise AssertionError("expected ValueError for block_samples=0")


def test_load_cf32_rejects_negative_n_samples():
    from tacet.loaders import cf32

    rng = np.random.default_rng(5)
    z = (rng.standard_normal(8) + 1j * rng.standard_normal(8)).astype(
        np.complex64
    )
    with tempfile.TemporaryDirectory() as td:
        path = _write_cf32(td, "x.cf32", z)
        try:
            cf32.load_cf32(path, n_samples=-1)
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
