"""Inter-channel phase coherence characterization for the Pluto+ dual RX.

The Pluto+ (AD9363) exposes two RX channels sharing one LO and one sample
clock. This module measures how stable the inter-channel phase difference
phi12(t) is, which bounds the maximum coherent processing interval (CPI)
usable in passive-radar cross-ambiguity processing. The headline number is
the largest window length for which std(phi12) stays below a threshold
(10 deg by default).

Everything here is analysis-only: no SDR is touched, no pyadi-iio import.
The receive-only rule applies to the whole project; nothing in this module
can enable any TX path.
"""

import json
import os
from typing import Optional

import numpy as np
from scipy import signal

__all__ = [
    "synthetic_tone_pair",
    "downconvert_decimate",
    "phase_difference",
    "window_phase_stats",
    "drift_rate",
    "allan_deviation",
    "integer_sample_offset",
    "iq_imbalance_tone",
    "noise_floor_dbfs",
    "max_cpi_window",
    "write_calibration",
]


# --------------------------------------------------------------------------
# Synthetic generator (tests and hardware-free notebook fallback)
# --------------------------------------------------------------------------


def synthetic_tone_pair(
    fs: float,
    duration_s: float,
    f0: float,
    phase_offset_deg: float,
    drift_deg_per_min: float = 0.0,
    noise_sigma: float = 1e-3,
    lag_samples: int = 0,
    amp_imbalance_db: float = 0.0,
    phase_skew_deg: float = 0.0,
):
    """Generate two complex tones with a controlled inter-channel relationship.

    The pair satisfies, by construction::

        phi12(t) = angle(z1 * conj(z2)) = phase_offset_deg + drift * t/60

    Parameters
    ----------
    lag_samples:
        Integer delay applied so that z2 lags z1 by ``lag_samples`` samples
        (negative values make z2 lead). Sign convention matches
        :func:`integer_sample_offset`.
    amp_imbalance_db, phase_skew_deg:
        IQ impairment applied to z2 (I gain vs Q gain in dB, quadrature
        skew in degrees), for :func:`iq_imbalance_tone` testing.
    noise_sigma:
        Std of independent complex Gaussian noise added to each channel.
    """
    rng = np.random.default_rng(0)
    lag = int(lag_samples)
    n = int(round(fs * duration_s))
    total = n + abs(lag)
    t_all = np.arange(total) / fs
    s = np.exp(1j * 2.0 * np.pi * f0 * t_all).astype(np.complex64)

    if lag >= 0:
        z1 = s[lag:].copy()  # z1[i] = s[i + lag]
        z2 = s[:n].copy()  # z2[i] = s[i]        -> z2 lags z1 by +lag
    else:
        z1 = s[:n].copy()
        z2 = s[-lag:].copy()  # z2[i] = s[i + |lag|] -> z2 leads

    t = np.arange(n) / fs
    # Drift is centered on the capture span so that mean(phi12) equals
    # phase_offset_deg exactly (the linear term averages to zero).
    t_centered = t - t[-1] / 2.0
    rot = np.exp(
        -1j
        * np.deg2rad(
            phase_offset_deg + drift_deg_per_min * (t_centered / 60.0)
        )
    )
    z2 = z2 * rot

    if amp_imbalance_db != 0.0 or phase_skew_deg != 0.0:
        g = 10.0 ** (amp_imbalance_db / 20.0)
        eps = np.deg2rad(phase_skew_deg)
        i_part = z2.real
        q_part = z2.imag
        z2 = (g * i_part + 1j * (q_part * np.cos(eps) + i_part * np.sin(eps))).astype(
            np.complex64
        )

    if noise_sigma > 0:
        z1 = z1 + noise_sigma * (
            rng.standard_normal(n) + 1j * rng.standard_normal(n)
        )
        z2 = z2 + noise_sigma * (
            rng.standard_normal(n) + 1j * rng.standard_normal(n)
        )

    return z1.astype(np.complex64), z2.astype(np.complex64)


# --------------------------------------------------------------------------
# Streaming downconversion and decimation
# --------------------------------------------------------------------------


class _DecimatorStage:
    """FIR lowpass + integer decimation with filter state across blocks."""

    def __init__(self, fs_in: float, decim: int, cutoff: float, stop_db: float,
                 width: float):
        numtaps, beta = signal.kaiserord(stop_db, width / (fs_in / 2.0))
        numtaps = max(15, min(int(numtaps) | 1, 4001))
        self.b = signal.firwin(numtaps, cutoff, window=("kaiser", beta), fs=fs_in)
        self.decim = decim
        self.zi = np.zeros(numtaps - 1, dtype=np.complex128)
        self.count = 0  # total samples filtered so far (for decimation phase)

    def process(self, block: np.ndarray) -> np.ndarray:
        y, self.zi = signal.lfilter(
            self.b, 1.0, block.astype(np.complex128), zi=self.zi
        )
        first = (self.decim - (self.count % self.decim)) % self.decim
        out = y[first :: self.decim]
        self.count += y.size
        return out


def _factor(total: int, cap: int = 64) -> list:
    """Factor ``total`` into decimation factors of at most ``cap`` each."""
    factors = []
    rem = int(total)
    while rem > 1:
        d = min(cap, rem)
        while rem % d != 0:
            d -= 1
        factors.append(d)
        rem //= d
    return factors


def downconvert_decimate(
    x,
    fs: float,
    f_offset: float,
    bw_hz: float = 3000.0,
    fs_out: float = 5000.0,
    block_samples: Optional[int] = None,
) -> np.ndarray:
    """Downconvert a narrowband component to baseband and decimate.

    Mixes ``x`` by ``exp(-2j*pi*f_offset*t)``, lowpass-filters to ``bw_hz``
    and decimates to the integer rate ``fs / round(fs / fs_out)``.

    Block-streaming safe: pass a np.memmap or ndarray with ``block_samples``
    set and the NCO phase and FIR states are carried across blocks, giving
    bit-comparable results to the full-array path (``block_samples=None``).
    Returns complex64 baseband samples at the decimated rate.
    """
    x = np.asarray(x)
    d_total = max(int(round(fs / fs_out)), 1)
    factors = _factor(d_total)

    stages = []
    fs_i = fs
    for idx, d in enumerate(factors):
        fs_after = fs_i / d
        nyq_after = fs_after / 2.0
        if idx == len(factors) - 1:
            cutoff = min(bw_hz / 2.0, 0.4 * nyq_after)
            width = 0.2 * nyq_after
            stop_db = 60.0
        else:
            cutoff = 0.35 * nyq_after
            width = 0.3 * nyq_after
            stop_db = 40.0
        stages.append(_DecimatorStage(fs_i, d, cutoff, stop_db, width))
        fs_i = fs_after

    inc = -2.0 * np.pi * f_offset / fs
    phase = 0.0
    n_in = x.size
    block = n_in if block_samples is None else int(block_samples)

    out_chunks = []
    for start in range(0, n_in, block):
        seg = np.asarray(x[start : start + block])
        m = seg.size
        ph = np.mod(phase + inc * np.arange(m), 2.0 * np.pi)
        mixed = seg.astype(np.complex64) * np.exp(1j * ph).astype(np.complex64)
        phase = float(np.mod(phase + inc * m, 2.0 * np.pi))
        cur = mixed
        for st in stages:
            cur = st.process(cur)
        if cur.size:
            out_chunks.append(np.asarray(cur, dtype=np.complex64))

    if not out_chunks:
        return np.zeros(0, dtype=np.complex64)
    return np.concatenate(out_chunks)


# --------------------------------------------------------------------------
# Phase metrics
# --------------------------------------------------------------------------


def phase_difference(z1, z2) -> np.ndarray:
    """Unwrapped inter-channel phase phi12(t) in radians.

    Using the product z1 * conj(z2) before unwrapping avoids unwrapping the
    two channels independently (their absolute phase is not meaningful).
    """
    d = np.asarray(z1, dtype=np.complex128) * np.conj(np.asarray(z2, dtype=np.complex128))
    return np.unwrap(np.angle(d))


def window_phase_stats(phi12, fs_phi: float, windows_s=(0.01, 0.05, 0.1, 0.5, 1.0)) -> dict:
    """Mean and std of phi12 within windows of several lengths.

    Returns ``{window_s: {n_windows, mean_deg, std_deg}}`` where mean/std are
    averaged across the complete windows of that length (trailing partial
    windows dropped). Long windows penalized by drift grow in std; that is
    the metric, no detrending is applied.
    """
    phi = np.asarray(phi12)
    stats = {}
    for w in windows_s:
        n_per = int(round(w * fs_phi))
        if n_per < 1:
            continue
        m = phi.size // n_per
        if m < 1:
            continue
        seg = phi[: m * n_per].reshape(m, n_per)
        means = np.rad2deg(np.mean(seg, axis=1))
        stds = np.rad2deg(np.std(seg, axis=1))
        stats[float(w)] = {
            "n_windows": int(m),
            "mean_deg": float(np.mean(means)),
            "std_deg": float(np.mean(stds)),
        }
    return stats


def drift_rate(phi12, fs_phi: float):
    """Linear drift of phi12 as (slope_deg_per_min, slope_stderr_deg_per_min)."""
    phi = np.asarray(phi12)
    t = np.arange(phi.size) / fs_phi
    (slope, _intercept), cov = np.polyfit(t, phi, 1, cov=True)
    slope_deg_min = float(np.rad2deg(slope) * 60.0)
    err_deg_min = float(np.rad2deg(np.sqrt(cov[0, 0])) * 60.0)
    return slope_deg_min, err_deg_min


def allan_deviation(phi12, fs_phi: float):
    """Overlapping Allan deviation of phase samples.

    For phase samples x[i] at spacing tau0 = 1/fs_phi and integer m::

        adev(m*tau0) = sqrt( mean_i( (x[i+2m] - 2*x[i+m] + x[i])**2 )
                             / (2 * (m*tau0)**2) )

    Only averaging times with at least 10 complete second differences are
    returned. White phase noise gives the familiar tau**-1 slope.
    """
    phi = np.asarray(phi12, dtype=np.float64)
    n = phi.size
    m_max = (n - 10) // 2
    if m_max < 1:
        raise ValueError("not enough samples for Allan deviation")
    ms = np.unique(
        np.clip(np.round(np.logspace(0, np.log10(m_max), 60)), 1, m_max).astype(int)
    )
    taus = []
    adevs = []
    for m in ms:
        second_diff = phi[2 * m :] - 2.0 * phi[m : -m] + phi[: -2 * m]
        tau = m / fs_phi
        adevs.append(float(np.sqrt(np.mean(second_diff**2) / (2.0 * tau**2))))
        taus.append(tau)
    return taus, adevs


def integer_sample_offset(x1, x2, segment_samples: Optional[int] = None) -> int:
    """Integer sample delay between two channels via FFT cross-correlation.

    Sign convention: positive ``lag`` means x2 LAGS x1, i.e.
    ``x2[n] ~= x1[n - lag]``. Only the first ``segment_samples`` of each
    stream are used.

    Note: correlation localizes a delay only if the signals carry bandwidth
    of order 1/lag or more. Two pure tones of identical frequency cannot be
    aligned this way (flat correlation magnitude); real captures (modulated
    broadcast plus noise) are fine. Test with bandlimited noise, not tones.
    """
    a = np.asarray(x1)
    b = np.asarray(x2)
    if segment_samples is None:
        segment_samples = min(a.size, b.size)
    s = int(min(segment_samples, a.size, b.size))
    c = signal.correlate(b[:s], a[:s], mode="full", method="fft")
    return int(np.argmax(np.abs(c)) - (s - 1))


# --------------------------------------------------------------------------
# Per-channel calibration metrics
# --------------------------------------------------------------------------


def iq_imbalance_tone(z) -> dict:
    """DC offset, IQ amplitude imbalance and quadrature skew from a strong tone.

    ``amp_imbalance_db`` = 20*log10(std_I/std_Q) after DC removal.
    ``phase_skew_deg`` = asin(corr(I, Q)) in degrees.
    ``image_ratio_db`` = dB ratio of the spectral image (-f0) vs the tone
    (+f0) measured on a windowed FFT of up to 2**16 samples.
    """
    z = np.asarray(z)
    s = min(z.size, 1 << 16)
    seg = z[:s].astype(np.complex128)
    dc = complex(np.mean(seg))
    i_part = seg.real - dc.real
    q_part = seg.imag - dc.imag
    amp_db = float(20.0 * np.log10(np.std(i_part) / np.std(q_part)))
    r = float(np.corrcoef(i_part, q_part)[0, 1])
    skew_deg = float(np.degrees(np.arcsin(np.clip(r, -1.0, 1.0))))
    spec = np.abs(np.fft.fft((seg - dc) * np.hanning(s)))
    k_peak = int(np.argmax(spec))
    k_image = (s - k_peak) % s
    if spec[k_peak] > 0:
        image_db = float(20.0 * np.log10(spec[k_image] / spec[k_peak]))
    else:
        image_db = float("nan")
    return {
        "dc": dc,
        "amp_imbalance_db": amp_db,
        "phase_skew_deg": skew_deg,
        "image_ratio_db": image_db,
    }


def noise_floor_dbfs(z) -> float:
    """Noise floor in dB below the strongest spectral component.

    Median of the windowed periodogram power relative to its peak. For
    recordings whose full scale is normalized, this reads as dBFS.
    """
    z = np.asarray(z)
    s = min(z.size, 1 << 16)
    seg = z[:s].astype(np.complex128)
    psd = np.abs(np.fft.fft(seg * np.hanning(s))) ** 2
    return float(10.0 * np.log10(np.median(psd) / psd.max()))


def max_cpi_window(window_stats_dict, threshold_deg: float = 10.0) -> Optional[float]:
    """Largest window length (s) whose std(phi12) stays below ``threshold_deg``.

    None if no window qualifies. This is the CPI bound the coherence
    experiment reports.
    """
    for w in sorted(window_stats_dict, reverse=True):
        if window_stats_dict[w]["std_deg"] < threshold_deg:
            return float(w)
    return None


def write_calibration(cal: dict, path) -> None:
    """Write a calibration snapshot dict as pretty JSON, creating parents."""
    path = str(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cal, fh, indent=2)
        fh.write("\n")
