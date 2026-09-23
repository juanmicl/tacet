"""DVDA twin-pair detector for ELRS D-mode double-send links.

ExpressLRS D-rates transmit every payload twice (DVDA) on the same FHSS
channel about 1 ms apart (interval 1000 us, numOfSends 2; firmware values
cited, GPL-3.0, no code copied). The detector matches that structure:
channelize into 1 MHz complex channels, gate bursts with a Pfa-calibrated
energy mask, pair same-channel segments in the 0.7-1.4 ms window, and
correlate each pair coherently. The block statistic is the best pair
peak; two nearly identical complex envelopes 1 ms apart are the DVDA
fingerprint (WiFi re-scrambles retransmitted PDUs, so envelopes do not
repeat). Pure functions; thresholds derive from Pfa only.
"""

import numpy as np
from scipy import stats


def channelize(z, fs, channel_hz=1e6):
    """Split a complex block into equal-width frequency channels.

    Z = fft(z); consecutive bin groups of equal width; per group ifft
    gives the channel's complex baseband at fs_ch = fs / n_ch. Channel k
    is centered at (k - n_ch/2 + 0.5) * channel_hz relative to the
    capture center. len(z) must be a multiple of n_ch.
    """
    z = np.asarray(z)
    n_ch = int(fs // channel_hz)
    if n_ch < 1:
        raise ValueError(f"channel_hz {channel_hz} too wide for fs {fs}")
    if z.size % n_ch != 0:
        raise ValueError(
            f"len(z) {z.size} must be a multiple of n_channels {n_ch}"
        )
    Z = np.fft.fftshift(np.fft.fft(z))
    grp = Z.size // n_ch
    channels = np.empty((n_ch, grp), dtype=np.complex64)
    for k in range(n_ch):
        channels[k] = np.fft.ifft(Z[k * grp : (k + 1) * grp]).astype(
            np.complex64
        )
    return channels, fs / n_ch


def energy_mask(ch, fs_ch, pfa=1e-3, bin_us=50.0):
    """Pfa-calibrated energy mask over integrated power bins.

    A bin sums bin_n iid exponential powers (Gamma(bin_n, theta)), so the
    threshold is the exact gamma quantile scaled by the mean estimate:
    T = gamma.ppf(1-pfa, a=bin_n) * mean_power / bin_n. Composability
    contract: always feed this the 100 ms block slice of a channel.
    """
    bin_n = max(1, int(round(bin_us * fs_ch / 1e6)))
    ch = np.asarray(ch)
    n_bins = ch.size // bin_n
    power = (np.abs(ch[: n_bins * bin_n]) ** 2).reshape(n_bins, bin_n).sum(
        axis=1
    )
    mean_power = float(power.mean())
    threshold = (
        float(stats.gamma.ppf(1.0 - pfa, a=bin_n)) * mean_power / bin_n
    )
    return power > threshold, power


def segments(mask, min_bins=4, merge_gap_bins=2):
    """Runs of True with small-gap merging; indices are ENERGY-BIN index
    pairs (i0, i1). The channel slice of a segment is
    ch[i0 * bin_n : (i1 + 1) * bin_n]."""
    idx = np.nonzero(mask)[0]
    if idx.size == 0:
        return []
    breaks = np.nonzero(np.diff(idx) > merge_gap_bins + 1)[0]
    out = []
    for run in np.split(idx, breaks + 1):
        if run.size >= min_bins:
            out.append((int(run[0]), int(run[-1])))
    return out


def pair_statistic(ch, seg_a, seg_b, bin_n, max_lag=75):
    """Normalized complex cross-correlation of two segments' baseband.

    Aligned by segment start, searched over +/- max_lag samples (75 us
    at 1 Msps covers the 50 us energy-bin start quantization). Returns
    peak (max |corr| in window), floor (median |corr| in window), both
    normalized by the product of norms, and the best lag in samples.
    """
    a = ch[seg_a[0] * bin_n : (seg_a[1] + 1) * bin_n]
    b = ch[seg_b[0] * bin_n : (seg_b[1] + 1) * bin_n]
    n = min(a.size, b.size)
    if n < 2 * max_lag + 2:
        return {"peak": 0.0, "floor": 0.0, "lag_samples": 0}
    a = a[:n].astype(np.complex128)
    b = b[:n].astype(np.complex128)
    nrm = float(np.linalg.norm(a) * np.linalg.norm(b))
    if nrm == 0.0:
        return {"peak": 0.0, "floor": 0.0, "lag_samples": 0}
    nfft = 1 << int(np.ceil(np.log2(2 * n - 1)))
    corr = np.fft.ifft(np.fft.fft(a, nfft) * np.conj(np.fft.fft(b, nfft)))
    mag = np.abs(np.r_[corr[-max_lag:], corr[: max_lag + 1]]) / nrm
    k = int(np.argmax(mag))
    return {
        "peak": float(mag[k]),
        "floor": float(np.median(mag)),
        "lag_samples": int(k - max_lag),
    }


def detect_block(z, fs, pfa=1e-3, pair_ms=(0.7, 1.4), channel_hz=1e6,
                 bin_us=50.0):
    """Run the full chain over one analysis block (design point 100 ms).

    Returns n_pairs, max_pair_peak (the block detection statistic),
    best_pair_gap_ms, best_channel, n_segments, n_channels.
    """
    channels, fs_ch = channelize(z, fs, channel_hz)
    bin_n = max(1, int(round(bin_us * fs_ch / 1e6)))
    bin_ms = bin_us / 1000.0
    out = {
        "n_pairs": 0,
        "max_pair_peak": 0.0,
        "best_pair_gap_ms": None,
        "best_channel": None,
        "n_segments": 0,
        "n_channels": int(channels.shape[0]),
    }
    for k in range(channels.shape[0]):
        mask, _ = energy_mask(channels[k], fs_ch, pfa=pfa, bin_us=bin_us)
        segs = segments(mask)
        out["n_segments"] += len(segs)
        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                gap_ms = (segs[j][0] - segs[i][0]) * bin_ms
                if not (pair_ms[0] <= gap_ms <= pair_ms[1]):
                    continue
                stat = pair_statistic(channels[k], segs[i], segs[j], bin_n)
                out["n_pairs"] += 1
                if stat["peak"] > out["max_pair_peak"]:
                    out["max_pair_peak"] = stat["peak"]
                    out["best_pair_gap_ms"] = float(gap_ms)
                    out["best_channel"] = int(k)
    return out
