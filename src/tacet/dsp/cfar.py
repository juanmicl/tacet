"""Data-driven CFAR noise-floor estimation over spectrogram matrices.

Complements tacet.dsp.features: spectrograms are fftshifted on the
frequency axis, power is linear, and the central DC bins never count
(2 * features.DC_EXCLUDE_BINS + 1 rows, mirroring features' own mask).

The noise floor is the per-time-slice MEDIAN of eligible bin powers.
Under complex white Gaussian noise a bin's power is exponentially
distributed with mean mu, whose median is mu*ln(2); pfa_threshold
accounts for that factor explicitly.
"""

import numpy as np

from tacet.dsp.features import DC_EXCLUDE_BINS


def _eligible_rows(n_freq):
    """Boolean mask over fftshifted frequency rows; central DC rows False."""
    mask = np.ones(n_freq, dtype=bool)
    center = n_freq // 2
    mask[center - DC_EXCLUDE_BINS : center + DC_EXCLUDE_BINS + 1] = False
    return mask


def noise_floor(S):
    """Per-time-slice noise floor: median over eligible frequency bins.

    S: (n_freq, n_time) linear-power spectrogram, fftshifted on axis 0.
    Returns (n_time,) float64. The median tolerates up to ~50% of bins
    carrying signal per slice; beyond that the floor sits on the signal,
    which suppresses detections in that slice only (conservative failure).
    """
    S = np.asarray(S)
    if S.ndim != 2:
        raise ValueError(f"S must be 2-D (n_freq, n_time), got shape {S.shape}")
    return np.median(S[_eligible_rows(S.shape[0]), :], axis=0)


def pfa_threshold(floor, pfa):
    """Bin-power threshold for a target per-bin false-alarm probability.

    The floor estimates the MEDIAN of the exponential bin-power
    distribution; median = mean * ln(2), so
    P(power > gamma * floor) = exp(-gamma * ln(2)) and a target pfa gives
    gamma = -ln(pfa) / ln(2)  (6.64 at pfa = 0.01).
    """
    if not 0.0 < pfa < 1.0:
        raise ValueError(f"pfa must be in (0, 1), got {pfa}")
    floor = np.asarray(floor, dtype=np.float64)
    return (-np.log(pfa) / np.log(2.0)) * floor


def detect_bins(S, pfa):
    """Boolean detection matrix S > threshold, DC rows forced False."""
    S = np.asarray(S)
    if S.ndim != 2:
        raise ValueError(f"S must be 2-D (n_freq, n_time), got shape {S.shape}")
    detected = S > pfa_threshold(noise_floor(S), pfa)[np.newaxis, :]
    detected[~_eligible_rows(S.shape[0]), :] = False
    return detected
