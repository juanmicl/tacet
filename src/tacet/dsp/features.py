"""P0 signature features: bandwidth, duty cycle, flatness, bursts.

Two layers: spectral helpers computed once per segment (layer A), and pure
functions over those arrays (layer B). The HackRF center DC spike is
removed/excluded before any power statistic (remove_dc + central bins).
All frequency axes are fftshifted (sorted, DC at the center).
"""

import numpy as np
from scipy import signal

DC_EXCLUDE_BINS = 4


def remove_dc(x):
    """Subtract the per-call complex mean (call per segment)."""
    x = np.asarray(x)
    return (x - np.mean(x)).astype(x.dtype)


def spectrogram(x, fs, nfft=1024, noverlap=0):
    """Linear-PSD spectrogram on the fftshifted axis."""
    f, t, S = signal.spectrogram(
        np.asarray(x), fs=fs, window="hann", nperseg=nfft,
        noverlap=noverlap, nfft=nfft, mode="psd", return_onesided=False,
    )
    return np.fft.fftshift(f), t, np.fft.fftshift(S, axes=0)


def spectrogram_db(x, fs, nfft=1024, noverlap=0):
    """Plot helper: dB spectrogram on the fftshifted axis."""
    f, t, S = spectrogram(x, fs, nfft=nfft, noverlap=noverlap)
    return f, t, 10.0 * np.log10(S + 1e-30)


def welch_psd(x, fs, nfft=1024):
    """Linear PSD on the fftshifted axis. dB conversion is plot-time only."""
    f, p = signal.welch(
        np.asarray(x), fs=fs, window="hann", nperseg=nfft, nfft=nfft,
        return_onesided=False, scaling="spectrum",
    )
    return np.fft.fftshift(f), np.fft.fftshift(p)


def time_envelope(S):
    """Per-time-bin peak power over frequency bins, DC center excluded."""
    S = np.asarray(S)
    n_f = S.shape[0]
    mid = n_f // 2
    mask = np.ones(n_f, dtype=bool)
    mask[max(0, mid - DC_EXCLUDE_BINS) : mid + DC_EXCLUDE_BINS + 1] = False
    return S[mask].max(axis=0)
