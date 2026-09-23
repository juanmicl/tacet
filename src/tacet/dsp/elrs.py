"""Per-block ELRS detection statistics over 2.4 GHz capture windows.

Presence statistics come from a per-bin-Pfa detection matrix (default
pfa=0.01). Attribution statistics (hopping vs stationary occupancy) come
from a STRICTER matrix (default pfa_attr=1e-4): at pfa=0.01 the raw
detection pattern saturates on noise (bin_diversity -> 1, turnover ->
~0.995 for pure noise), so hopping structure is only visible at the
stricter operating point. Pure functions; no I/O; the caller picks the
block length (design point ~100 ms at 20 Msps).
"""

import numpy as np

from tacet.dsp import cfar, features


def analyze_block(z, fs, pfa=0.01, nfft=1024, pfa_attr=1e-4):
    """Statistics for one analysis block of complex IQ.

    Returns a dict with transparency fields (n_slices, n_bins_eligible,
    floor_median_per_slice), presence statistics from the pfa matrix
    (max_occupied_fraction, excess_db) and attribution statistics from
    the pfa_attr matrix (bin_diversity_attr, turnover_attr).
    """
    z = features.remove_dc(z)
    _, _, S = features.spectrogram(z, fs, nfft=nfft)
    floor = cfar.noise_floor(S)
    n_elig = int(S.shape[0] - (2 * features.DC_EXCLUDE_BINS + 1))

    D = cfar.detect_bins(S, pfa)
    occupied_fraction = D.sum(axis=0) / n_elig
    ratio = S / floor[np.newaxis, :]
    center = S.shape[0] // 2
    ratio[center - 4 : center + 5, :] = 0.0  # DC rows excluded from excess

    D_attr = cfar.detect_bins(S, pfa_attr)
    prev, nxt = D_attr[:, :-1], D_attr[:, 1:]
    union = (prev | nxt).sum(axis=0)
    inter = (prev & nxt).sum(axis=0)
    nonempty = union > 0
    if nonempty.any():
        jaccard_distance = 1.0 - inter[nonempty] / union[nonempty]
        turnover = float(jaccard_distance.mean())
    else:
        turnover = 0.0

    return {
        "n_slices": int(S.shape[1]),
        "n_bins_eligible": n_elig,
        "floor_median_per_slice": floor,
        "max_occupied_fraction": float(occupied_fraction.max()),
        "excess_db": float(10.0 * np.log10(ratio.max())),
        "bin_diversity_attr": float(D_attr.any(axis=1).mean()),
        "turnover_attr": turnover,
    }
