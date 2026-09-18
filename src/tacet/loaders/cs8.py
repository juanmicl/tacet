"""cs8 (complex int8, interleaved I/Q) loading — the HackRF native format.

hackrf_transfer -r writes signed 8-bit interleaved I/Q pairs, one file per
recording. Samples keep their raw ADC counts (no normalization).
"""

from collections.abc import Iterator
from typing import Optional

import numpy as np


def load_cs8(path: str, n_samples: Optional[int] = None) -> np.ndarray:
    """Load a headerless cs8 file as complex64 (memmap-based).

    n_samples limits the complex samples read; a trailing half-sample on an
    odd byte count is ignored.
    """
    raw = np.memmap(path, dtype=np.int8, mode="r")
    n_avail = raw.size // 2
    n = n_avail if n_samples is None else min(n_samples, n_avail)
    iq = np.asarray(raw[: 2 * n]).reshape(n, 2)
    z = np.empty(n, dtype=np.complex64)
    z.real = iq[:, 0]
    z.imag = iq[:, 1]
    return z


def iter_cs8_blocks(path: str, block_samples: int) -> Iterator[np.ndarray]:
    """Yield successive complex64 blocks; last block may be shorter."""
    if block_samples <= 0:
        raise ValueError("block_samples must be positive")
    raw = np.memmap(path, dtype=np.int8, mode="r")
    n_avail = raw.size // 2
    for start in range(0, n_avail, block_samples):
        n = min(block_samples, n_avail - start)
        iq = np.asarray(raw[2 * start : 2 * (start + n)]).reshape(n, 2)
        z = np.empty(n, dtype=np.complex64)
        z.real = iq[:, 0]
        z.imag = iq[:, 1]
        yield z
