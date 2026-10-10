"""Self-tests for the VHF FM broadcast band: mapping + schema round trip."""

from tacet.loaders import manifest as manifest_mod
from tacet.loaders import migration


def _must_raise(fn):
    try:
        fn()
    except ValueError:
        return
    raise AssertionError("expected ValueError, got none")


def test_fm_band_mapping():
    f = migration.band_for_freq_hz
    assert f(98.5e6) == "VHF FM broadcast"
    # 87.5 MHz inclusive lower edge, 108 MHz exclusive upper edge
    assert f(87.5e6) == "VHF FM broadcast"
    assert f(107.999e6) == "VHF FM broadcast"
    _must_raise(lambda: f(108e6))
    _must_raise(lambda: f(87.4e6))


def test_fm_entry_schema_round_trip():
    # Minimal fm_carrier coherence-cal entry on the new band must validate.
    e = {
        "id": "20261010T000000Z_fm-coherence_000",
        "schema_version": "2.0",
        "timestamp": "2026-10-10T00:00:00+00:00",
        "device": "pluto_plus",
        "protocol": "fm_carrier",
        "purpose": "coherence_cal",
        "center_freq_hz": 98.5e6,
        "sample_rate_sps": 20000000.0,
        "format": "cs16",
        "duration_s": 5.0,
        "channels": [
            {"path": "data/coherence_cal/s/fm-coherence_000.cs16",
             "sha256": "ab" * 32}
        ],
        "antenna": "ANT500 telescopic",
        "operator_notes": "",
        "band": "VHF FM broadcast",
        "label": "interference",
        "condition": "bench",
        "distance_m": None,
        "environment": "indoor_bench",
        "los": None,
        "contributor": "juanmicl",
        "source": "own_capture",
        "consent": "private",
        "site_anonymized": True,
    }
    manifest_mod.validate_entry(e)


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
