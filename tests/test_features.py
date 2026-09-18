"""Synthetic self-tests for cs8 loader and tacet.dsp.features."""

import os
import tempfile

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
