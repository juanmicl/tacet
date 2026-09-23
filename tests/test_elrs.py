"""Tests for the ELRS per-block detector (synthetic hopping signals)."""

import numpy as np

FS = 4e6          # 4 Msps test rig: fast spectrograms, realistic ratios
NFFT = 1024
BLOCK_S = 0.1     # 100 ms = 400_000 samples = ~390 slices


def _hopping_signal(rng, n_channels=30, packet_s=0.001, duty_packets=30,
                     chirp_bw=400e3, amp=40.0):
    """Seeded hopping chirp train: packets on pseudo-random channels."""
    n_block = int(BLOCK_S * FS)
    z = np.zeros(n_block, dtype=np.complex64)
    pkt_n = int(packet_s * FS)
    slots = np.linspace(0, n_block - pkt_n, duty_packets, dtype=int)
    chans = rng.integers(0, n_channels, size=duty_packets)
    span = FS / 2 * 0.9
    for slot, ch in zip(slots, chans):
        f0 = -span + (2 * span) * ch / max(n_channels - 1, 1)
        t = np.arange(pkt_n) / FS
        k = chirp_bw / packet_s
        phase = 2 * np.pi * (f0 * t + 0.5 * k * t * t)
        z[slot : slot + pkt_n] += (amp * np.exp(1j * phase)).astype(
            np.complex64
        )
    return z


def _stationary_signal(rng):
    """Continuous fixed-frequency carrier on channel 0 (no hopping).

    Deviation from the planning brief (which specified a duty-cycled
    chirp restarting at f0 every packet): that signal is geometrically
    indistinguishable from hopping under the turnover statistic. Each
    packet restarts its 400 kHz/ms sweep, so consecutive disjoint
    spectrogram slices (noverlap=0) share almost no bins (~0.91 churn),
    the same value hopping produces; the mandated assertion
    (stationary < 0.3 <= 0.5 < hopping) is unsatisfiable for any
    amplitude, pfa or window overlap because only the channel LABEL
    differs between the two signals. A persistent carrier is the
    physical model with genuinely stationary occupancy (e.g. an analog
    FPV video carrier) and yields turnover ~0.09 vs ~0.91 for hopping.
    """
    n_block = int(BLOCK_S * FS)
    z = np.zeros(n_block, dtype=np.complex64)
    span = FS / 2 * 0.9
    t = np.arange(n_block) / FS
    phase = 2 * np.pi * (-span) * t
    z += (40.0 * np.exp(1j * phase)).astype(np.complex64)
    return z


def _noise_block(rng):
    n_block = int(BLOCK_S * FS)
    return (
        rng.standard_normal(n_block) + 1j * rng.standard_normal(n_block)
    ).astype(np.complex64)


def test_hopping_detected_and_diverse():
    from tacet.dsp import elrs

    rng = np.random.default_rng(20260923)
    z_sig = _hopping_signal(rng) + _noise_block(rng)
    z_noise = _noise_block(rng)
    r_sig = elrs.analyze_block(z_sig, FS, nfft=NFFT)
    r_noise = elrs.analyze_block(z_noise, FS, nfft=NFFT)
    # Presence: ~100 occupied bins per packet slice vs the ~0.02 noise null.
    assert r_sig["max_occupied_fraction"] > 0.05, r_sig[
        "max_occupied_fraction"
    ]
    assert r_noise["max_occupied_fraction"] < 0.04, r_noise[
        "max_occupied_fraction"
    ]
    # Attribution: hopping hits bins across the band; noise stays sparse.
    assert (
        r_sig["bin_diversity_attr"] > 3.0 * r_noise["bin_diversity_attr"]
    ), (r_sig["bin_diversity_attr"], r_noise["bin_diversity_attr"])


def test_stationary_low_turnover_vs_hopping():
    from tacet.dsp import elrs

    rng = np.random.default_rng(20260923)
    z_hop = _hopping_signal(rng) + _noise_block(rng)
    z_stat = _stationary_signal(rng) + _noise_block(rng)
    r_hop = elrs.analyze_block(z_hop, FS, nfft=NFFT)
    r_stat = elrs.analyze_block(z_stat, FS, nfft=NFFT)
    assert r_hop["turnover_attr"] > 0.5, r_hop["turnover_attr"]
    assert r_stat["turnover_attr"] < 0.3, r_stat["turnover_attr"]


def test_noise_block_below_presence_bound():
    from tacet.dsp import elrs

    rng = np.random.default_rng(99)
    r = elrs.analyze_block(_noise_block(rng), FS, nfft=NFFT)
    # Null expectation ~0.02 (max of ~390 draws of Binomial(1015, 0.01)/1015)
    # plus margin; seeded so this is deterministic.
    assert r["max_occupied_fraction"] < 0.04, r["max_occupied_fraction"]
    assert r["excess_db"] > 0.0  # definitionally positive (max ratio > 1)


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
