"""cf32 (complex float32, interleaved I/Q) loading — DroneDetect-native format.

Files are headerless sequences of interleaved 32-bit float I/Q pairs (8 bytes
per complex sample). Samples keep their native float scale (no normalization).
"""

from collections.abc import Iterator
from typing import Optional

import numpy as np


def load_cf32(
    path: str, n_samples: Optional[int] = None, start: int = 0
) -> np.ndarray:
    """Load a headerless cf32 file as complex64 (memmap-based).

    n_samples limits the complex samples read from `start`; if fewer are
    available, fewer are returned. start is a complex-sample offset so large
    files (DroneDetect .dat are ~960 MB) can be read in windows without
    loading them whole. A trailing half-sample on an odd float count is
    ignored, and an empty file surfaces numpy's own memmap error.
    """
    if start < 0:
        raise ValueError("start must be non-negative")
    if n_samples is not None and n_samples < 0:
        raise ValueError("n_samples must be non-negative")
    raw = np.memmap(path, dtype=np.float32, mode="r")
    n_avail = max(raw.size // 2 - start, 0)
    n = n_avail if n_samples is None else min(n_samples, n_avail)
    iq = np.asarray(raw[2 * start : 2 * (start + n)]).reshape(n, 2)
    z = np.empty(n, dtype=np.complex64)
    z.real = iq[:, 0]
    z.imag = iq[:, 1]
    return z


def iter_cf32_blocks(path: str, block_samples: int) -> Iterator[np.ndarray]:
    """Return an iterator yielding successive complex64 blocks; last block may be shorter."""
    if block_samples <= 0:
        raise ValueError("block_samples must be positive")
    return _iter_cf32_blocks(path, block_samples)


def _iter_cf32_blocks(path: str, block_samples: int) -> Iterator[np.ndarray]:
    raw = np.memmap(path, dtype=np.float32, mode="r")
    n_avail = raw.size // 2
    for start in range(0, n_avail, block_samples):
        n = min(block_samples, n_avail - start)
        iq = np.asarray(raw[2 * start : 2 * (start + n)]).reshape(n, 2)
        z = np.empty(n, dtype=np.complex64)
        z.real = iq[:, 0]
        z.imag = iq[:, 1]
        yield z
