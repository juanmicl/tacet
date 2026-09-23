"""Tests for the DVDA twin-pair detector (synthetic FLRC pairs)."""

import numpy as np

FS_T = 20e6
SYM_RATE = 650e3
DEV_HZ = 162.5e3
CARRIER_HZ = 3.5e6  # lands in channel 13 of the 20x1 MHz grid
BLOCK_S = 0.1


def _gaussian_bt_fir(bt, sym_rate, fs):
    """Gaussian shaping FIR taps with the given BT at the symbol rate."""
    sym_n = int(round(fs / sym_rate))
    t = np.arange(-3 * sym_n, 3 * sym_n + 1) / fs
    sigma = np.sqrt(np.log(2.0)) / (2.0 * np.pi * bt * sym_rate)
    g = np.exp(-(t ** 2) / (2.0 * sigma ** 2))
    return g / g.sum()


def _flrc_packet(rng, n_bits=253, bt=1.0):
    """Approximate FLRC GMSK packet (~389 us): random bits -> +/-dev NRZ
    -> Gaussian FIR (BT=1.0) -> integrated phase -> complex64."""
    bits = (rng.integers(0, 2, n_bits) * 2 - 1).astype(np.float64)
    sym_n = int(round(FS_T / SYM_RATE))
    levels = np.repeat(bits, sym_n)
    g = _gaussian_bt_fir(bt, SYM_RATE, FS_T)
    freq = np.convolve(levels, g, mode="same") * DEV_HZ
    phase = 2.0 * np.pi * np.cumsum(freq) / FS_T
    return np.exp(1j * phase).astype(np.complex64)


def _block_with_packets(rng, packets, amp=8.0, noise=0.5):
    """100 ms noise block with packets injected at CARRIER_HZ offsets.

    packets: list of (packet_waveform, delay_ms).
    """
    n = int(BLOCK_S * FS_T)
    z = (
        rng.standard_normal(n) + 1j * rng.standard_normal(n)
    ).astype(np.complex64) * noise
    t_idx = np.arange(n)
    for wave, delay_ms in packets:
        s = int(delay_ms * 1e-3 * FS_T)
        lo, hi = s, min(s + wave.size, n)
        z[lo:hi] += (amp * wave[: hi - lo] * np.exp(
            2j * np.pi * CARRIER_HZ * t_idx[s:hi] / FS_T
        )).astype(np.complex64)
    return z


def test_twin_pair_detected():
    from tacet.dsp import dvda

    rng = np.random.default_rng(20260923)
    pkt = _flrc_packet(rng)
    z = _block_with_packets(rng, [(pkt, 10.0), (pkt, 11.0)])
    r = dvda.detect_block(z, FS_T)
    assert r["n_pairs"] >= 1, r
    assert r["max_pair_peak"] > 0.3, r["max_pair_peak"]
    assert r["best_pair_gap_ms"] is not None and abs(
        r["best_pair_gap_ms"] - 1.0
    ) < 0.15, r["best_pair_gap_ms"]


def test_different_content_low_peak():
    from tacet.dsp import dvda

    rng = np.random.default_rng(20260923)
    pkt_a = _flrc_packet(rng)
    pkt_b = _flrc_packet(rng)  # different random bits
    z_twin = _block_with_packets(rng, [(pkt_a, 10.0), (pkt_a, 11.0)])
    z_diff = _block_with_packets(rng, [(pkt_a, 10.0), (pkt_b, 11.0)])
    r_twin = dvda.detect_block(z_twin, FS_T)
    r_diff = dvda.detect_block(z_diff, FS_T)
    assert r_twin["max_pair_peak"] > 0.3, r_twin
    assert r_diff["max_pair_peak"] < 0.15, r_diff["max_pair_peak"]
    assert r_twin["max_pair_peak"] > 3.0 * r_diff["max_pair_peak"]


def test_single_burst_no_pairs():
    from tacet.dsp import dvda

    rng = np.random.default_rng(7)
    pkt = _flrc_packet(rng)
    z = _block_with_packets(rng, [(pkt, 10.0)])
    r = dvda.detect_block(z, FS_T)
    assert r["n_pairs"] == 0, r
    assert r["max_pair_peak"] == 0.0, r


def test_spacing_robustness():
    from tacet.dsp import dvda

    rng = np.random.default_rng(11)
    pkt = _flrc_packet(rng)
    z = _block_with_packets(rng, [(pkt, 10.0), (pkt, 11.25)])
    r = dvda.detect_block(z, FS_T)
    assert r["n_pairs"] >= 1, r
    assert r["max_pair_peak"] > 0.3, r["max_pair_peak"]


def test_noise_block_quiet():
    from tacet.dsp import dvda

    rng = np.random.default_rng(13)
    n = int(BLOCK_S * FS_T)
    z = (
        rng.standard_normal(n) + 1j * rng.standard_normal(n)
    ).astype(np.complex64) * 0.5
    r = dvda.detect_block(z, FS_T)
    assert r["max_pair_peak"] < 0.15, r
    assert r["n_pairs"] <= 2, r  # false segments at pfa 1e-3 are ~nil


def test_channelize_tone_placement():
    from tacet.dsp import dvda

    rng = np.random.default_rng(17)
    n = int(BLOCK_S * FS_T)
    t = np.arange(n) / FS_T
    z = (4.0 * np.exp(2j * np.pi * CARRIER_HZ * t)).astype(np.complex64)
    z += (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(
        np.complex64
    ) * 0.3
    channels, fs_ch = dvda.channelize(z, FS_T)
    powers = (np.abs(channels) ** 2).mean(axis=1)
    best = int(np.argmax(powers))
    # channel k centered at (k - 10 + 0.5) MHz; +3.5 MHz -> k = 13
    assert best == 13, (best, powers.argmax())
    assert fs_ch == 1e6, fs_ch


def test_energy_mask_pfa():
    from tacet.dsp import dvda

    rng = np.random.default_rng(19)
    n = 1_000_000
    ch = (
        rng.standard_normal(n) + 1j * rng.standard_normal(n)
    ).astype(np.complex64)
    # pfa 1e-2 (not 1e-3): the 4-sigma binomial band must be SMALL
    # relative to nominal for the test to catch a miscalibrated gamma
    # threshold (at 1e-3 with 20k bins the band is ~90% of nominal).
    pfa = 1e-2
    mask, _ = dvda.energy_mask(ch, 1e6, pfa=pfa)
    n_bins = mask.size
    emp = float(mask.mean())
    band = 4.0 * np.sqrt(pfa * (1 - pfa) / n_bins)
    assert abs(emp - pfa) < band, f"empirical {emp:.5f} vs nominal {pfa}"


def test_adjacent_ghost_suppressed():
    """Strong twin pair must not double-pair via adjacent-channel
    leakage ghosts: the ghost segments are weaker than their source."""
    from tacet.dsp import dvda

    rng = np.random.default_rng(23)
    pkt = _flrc_packet(rng)
    z = _block_with_packets(rng, [(pkt, 10.0), (pkt, 11.0)])
    r = dvda.detect_block(z, FS_T)
    assert r["n_pairs"] == 1, r
    assert r["best_channel"] == 13, r


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
