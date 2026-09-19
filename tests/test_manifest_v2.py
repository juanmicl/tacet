"""Self-tests for Manifest v2: schema, validator, migration, validate CLI."""

import json
import os
import tempfile
from pathlib import Path

from tacet.loaders import manifest as manifest_mod


def _v2_entry(**overrides):
    """A minimal schema-valid v2 entry (override or extend as needed)."""
    e = {
        "id": "20260919T004638Z_elrs-bench_000",
        "schema_version": "2.0",
        "timestamp": "2026-09-19T00:46:38+00:00",
        "device": "hackrf",
        "protocol": "elrs",
        "purpose": "signature_capture",
        "center_freq_hz": 2440000000.0,
        "sample_rate_sps": 20000000.0,
        "format": "cs8",
        "duration_s": 3.0,
        "channels": [
            {"path": "data/signature_capture/s/elrs-bench_000.cs8",
             "sha256": "ab" * 32}
        ],
        "antenna": "dual-band 2.4/5.8 SMA",
        "operator_notes": "bench ~1m",
        "band": "2.4 GHz ISM",
        "label": "elrs",
        "condition": "bench",
        "distance_m": 1.0,
        "environment": "indoor_bench",
        "los": True,
        "contributor": "juanmicl",
        "source": "own_capture",
        "consent": "private",
        "site_anonymized": True,
    }
    e.update(overrides)
    return e


def _must_raise(fn):
    try:
        fn()
    except ValueError:
        return
    raise AssertionError("expected ValueError, got none")


def test_v2_entry_validates():
    manifest_mod.validate_entry(_v2_entry())


def test_v2_rejects_unknown_field():
    # strict: no additional properties at entry level
    _must_raise(lambda: manifest_mod.validate_entry(_v2_entry(foo=1)))


def test_v2_rejects_bad_schema_version():
    _must_raise(lambda: manifest_mod.validate_entry(
        _v2_entry(schema_version="1.0")))


def test_v2_nullable_distance_and_los():
    e = _v2_entry(distance_m=None, los=None)
    manifest_mod.validate_entry(e)
    _must_raise(lambda: manifest_mod.validate_entry(
        _v2_entry(distance_m="far")))
    _must_raise(lambda: manifest_mod.validate_entry(_v2_entry(los="yes")))


def test_v2_band_label_condition_enums():
    manifest_mod.validate_entry(_v2_entry(band="5.8 GHz ISM",
                                          label="background",
                                          condition="tx_off"))
    # v1's overloaded class labels are no longer valid bands
    _must_raise(lambda: manifest_mod.validate_entry(_v2_entry(band="control")))
    _must_raise(lambda: manifest_mod.validate_entry(
        _v2_entry(band="5.1/5.8 GHz")))
    _must_raise(lambda: manifest_mod.validate_entry(_v2_entry(label="noise")))
    _must_raise(lambda: manifest_mod.validate_entry(
        _v2_entry(condition="unknown")))


def test_v1_entry_now_invalid():
    # a v1-shaped entry (no governance/experiment fields) fails v2
    v1 = _v2_entry()
    for key in ("schema_version", "band", "label", "condition", "distance_m",
                "environment", "los", "contributor", "source", "consent",
                "site_anonymized"):
        del v1[key]
    _must_raise(lambda: manifest_mod.validate_entry(v1))


def test_v2_governance_enums():
    manifest_mod.validate_entry(_v2_entry(source="public_dataset",
                                          consent="cc-by-4.0",
                                          site_anonymized=False))
    _must_raise(lambda: manifest_mod.validate_entry(_v2_entry(source="scraped")))
    _must_raise(lambda: manifest_mod.validate_entry(
        _v2_entry(consent="do-whatever")))
    _must_raise(lambda: manifest_mod.validate_entry(
        _v2_entry(site_anonymized="yes")))


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
