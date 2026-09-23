"""Tests for the data-driven CFAR noise-floor primitive."""

import numpy as np


def _cwgn_spectrogram(rng, nfft=1024, n_time=512, fs=20e6):
    """Seeded complex white gaussian noise -> linear-power spectrogram."""
    from tacet.dsp import features

    n = nfft * n_time
    z = (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(
        np.complex64
    )
    _, _, S = features.spectrogram(z, fs, nfft=nfft)
    return S


def _subsample_rows(n_freq, center, half_width=4, step=3):
    """Every 3rd frequency row, excluding the central DC block.

    Subsampling restores approximate independence between evaluated bins
    (Hann windowing correlates adjacent bins), which the binomial
    tolerance assumes.
    """
    return [
        i
        for i in range(0, n_freq, step)
        if abs(i - center) > half_width
    ]


def test_empirical_pfa_matches_nominal():
    from tacet.dsp import cfar

    rng = np.random.default_rng(20260923)
    S = _cwgn_spectrogram(rng)
    pfa = 0.01
    D = cfar.detect_bins(S, pfa)
    rows = _subsample_rows(D.shape[0], D.shape[0] // 2)
    emp = D[rows, :].mean()
    n = len(rows) * D.shape[1]
    band = 4.0 * np.sqrt(pfa * (1.0 - pfa) / n)
    assert abs(emp - pfa) < band, f"empirical {emp:.5f} vs nominal {pfa}"


def test_scale_invariance():
    from tacet.dsp import cfar

    rng = np.random.default_rng(20260923)
    S = _cwgn_spectrogram(rng)
    pfa = 0.01
    D1 = cfar.detect_bins(S, pfa)
    D2 = cfar.detect_bins(100.0 * S, pfa)
    rows = _subsample_rows(D1.shape[0], D1.shape[0] // 2)
    emp1 = D1[rows, :].mean()
    emp2 = D2[rows, :].mean()
    n = len(rows) * D1.shape[1]
    band = 4.0 * np.sqrt(pfa * (1.0 - pfa) / n)
    assert abs(emp1 - emp2) < band, f"{emp1:.5f} vs {emp2:.5f}"


def test_tone_detected_neighbors_off():
    from tacet.dsp import cfar

    rng = np.random.default_rng(11)
    S = _cwgn_spectrogram(rng).copy()
    floor_typ = np.median(S)
    row = 300  # far from the central DC block
    S[row, :] += 100.0 * floor_typ  # ~20 dB above the typical floor
    D = cfar.detect_bins(S, 0.01)
    assert D[row, :].mean() > 0.99, "tone row must fire"
    assert D[row - 1, :].mean() < 0.5, "lower neighbor must stay mostly off"
    assert D[row + 1, :].mean() < 0.5, "upper neighbor must stay mostly off"


def test_dc_rows_never_fire():
    from tacet.dsp import cfar

    rng = np.random.default_rng(12)
    S = _cwgn_spectrogram(rng).copy()
    center = S.shape[0] // 2
    S[center - 4 : center + 5, :] = 1e9  # huge central block
    D = cfar.detect_bins(S, 0.01)
    assert not D[center - 4 : center + 5, :].any(), "DC rows must never fire"


def test_median_floor_robust_to_contamination():
    from tacet.dsp import cfar

    rng = np.random.default_rng(13)
    S = _cwgn_spectrogram(rng).copy()
    clean = cfar.noise_floor(S)
    n_rows = int(0.20 * S.shape[0])  # 20% of frequency rows
    S[:n_rows, :] *= 1000.0  # strong signal contamination
    contaminated = cfar.noise_floor(S)
    # With 20% contamination the combined median sits at the 62.5th
    # percentile of the noise values (~1.4x the clean median for an
    # exponential distribution). Per-slice ratios carry order-statistic
    # noise (~6%), so assert on the aggregate, not every slice.
    assert np.median(contaminated) < 1.5 * np.median(clean), (
        "median floor broke"
    )


def test_input_validation():
    from tacet.dsp import cfar

    rng = np.random.default_rng(14)
    S = _cwgn_spectrogram(rng)
    for bad_pfa in (0.0, 1.0, -0.1, 1.1):
        try:
            cfar.pfa_threshold(np.array([1.0]), bad_pfa)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for pfa={bad_pfa}")
    try:
        cfar.detect_bins(S[0, :], 0.01)  # 1-D input
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for 1-D S")


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
