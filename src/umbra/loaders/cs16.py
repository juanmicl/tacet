"""cs16 (complex int16, interleaved I/Q) loading.

The Pluto+ streams raw int16 interleaved I/Q pairs; one file per channel.
Samples keep their raw ADC counts (no normalization): conversion to
complex64 only moves them into floating point.
"""

from collections.abc import Iterator
from typing import Optional

import numpy as np


def load_cs16(path: str, n_samples: Optional[int] = None) -> np.ndarray:
    """Load a headerless cs16 file as complex64.

    Parameters
    ----------
    path:
        File path.
    n_samples:
        Number of complex samples to read. ``None`` reads the whole file.
        If the file holds fewer samples, only what exists is returned.
    """
    raw = np.memmap(path, dtype=np.int16, mode="r")
    n_avail = raw.size // 2
    n = n_avail if n_samples is None else min(n_samples, n_avail)
    iq = np.asarray(raw[: 2 * n]).reshape(n, 2)
    z = np.empty(n, dtype=np.complex64)
    z.real = iq[:, 0]
    z.imag = iq[:, 1]
    return z


def iter_cs16_blocks(path: str, block_samples: int) -> Iterator[np.ndarray]:
    """Yield successive complex64 blocks of ``block_samples`` from a cs16 file.

    The last block may be shorter. Memory-mapped, so long recordings can be
    streamed without loading them whole.
    """
    if block_samples <= 0:
        raise ValueError("block_samples must be positive")
    raw = np.memmap(path, dtype=np.int16, mode="r")
    n_avail = raw.size // 2
    for start in range(0, n_avail, block_samples):
        n = min(block_samples, n_avail - start)
        iq = np.asarray(raw[2 * start : 2 * (start + n)]).reshape(n, 2)
        z = np.empty(n, dtype=np.complex64)
        z.real = iq[:, 0]
        z.imag = iq[:, 1]
        yield z
