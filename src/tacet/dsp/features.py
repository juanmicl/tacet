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


def _dc_mask(n: int) -> np.ndarray:
    mid = n // 2
    mask = np.ones(n, dtype=bool)
    mask[max(0, mid - DC_EXCLUDE_BINS) : mid + DC_EXCLUDE_BINS + 1] = False
    return mask


def occupied_bandwidth(freqs_hz, psd_linear, percent=99.0):
    """Minimum contiguous band holding `percent` of total power.

    The axis must be sorted (fftshifted). Energy split across the window
    edges is NOT wrapped: the reported band widens (surfacing the split
    instead of hiding it).
    """
    freqs = np.asarray(freqs_hz, dtype=float)
    p = np.clip(np.asarray(psd_linear, dtype=float), 0.0, None)
    total = p.sum()
    if total <= 0 or p.size < 2:
        return 0.0, float(freqs[0]), float(freqs[-1])
    cum = np.concatenate(([0.0], np.cumsum(p)))
    need = (percent / 100.0) * total
    best_w = freqs[-1] - freqs[0]
    best_lo, best_hi = float(freqs[0]), float(freqs[-1])
    for i in range(p.size):
        j = int(np.searchsorted(cum, cum[i] + need, side="left"))
        if j >= cum.size:
            break
        w = freqs[j - 1] - freqs[i]
        if w < best_w:
            best_w, best_lo, best_hi = float(w), float(freqs[i]), float(freqs[j - 1])
    return best_w, best_lo, best_hi


def spectral_flatness(psd_linear) -> float:
    """Geometric/arithmetic mean ratio on LINEAR power, DC bins excluded."""
    p = np.clip(np.asarray(psd_linear, dtype=float), 1e-30, None)
    p = p[_dc_mask(p.size)]
    return float(np.exp(np.mean(np.log(p))) / np.mean(p))


def duty_cycle(env, threshold_rel_db=-35.0) -> float:
    """Fraction of time bins whose envelope exceeds max + threshold."""
    env = np.asarray(env, dtype=float)
    peak = env.max()
    if peak <= 0:
        return 0.0
    thr = peak * 10.0 ** (threshold_rel_db / 10.0)
    return float(np.mean(env >= thr))


def burst_structure(env, t, threshold_rel_db=-35.0) -> dict:
    """On/off segmentation of the envelope; same threshold as duty_cycle."""
    env = np.asarray(env, dtype=float)
    t = np.asarray(t, dtype=float)
    peak = env.max()
    thr = peak * 10.0 ** (threshold_rel_db / 10.0) if peak > 0 else 0.0
    on = env >= thr
    bursts = []
    start = None
    for i, v in enumerate(on):
        if v and start is None:
            start = float(t[i])
        elif not v and start is not None:
            bursts.append((start, float(t[i])))
            start = None
    if start is not None:
        bursts.append((start, float(t[-1])))
    durs = [b - a for a, b in bursts]
    gaps = [bursts[i + 1][0] - bursts[i][1] for i in range(len(bursts) - 1)]
    return {
        "bursts": bursts,
        "mean_burst_s": float(np.mean(durs)) if durs else 0.0,
        "mean_gap_s": float(np.mean(gaps)) if gaps else 0.0,
    }


def rail_fraction(x) -> float:
    """Fraction of samples at the int8 rails (+-127): clipping QC."""
    x = np.asarray(x)
    rails = (np.abs(x.real) >= 127) | (np.abs(x.imag) >= 127)
    return float(np.mean(rails))
