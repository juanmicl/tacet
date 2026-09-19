"""Self-tests for Manifest v2: schema, validator, migration, validate CLI."""

import json
import os
import tempfile
from pathlib import Path

from tacet.loaders import manifest as manifest_mod
from tacet.loaders import migration

GOLDEN = Path(__file__).resolve().parent / "golden" / "manifest_v2.json"

# Fields added by the v2 migration (stripped to derive a v1 fixture).
V2_ONLY_FIELDS = (
    "schema_version", "label", "condition", "distance_m", "environment",
    "los", "elrs_profile", "contributor", "source", "consent",
    "site_anonymized",
)


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


# --- Migration (v1 flat list -> v2 wrapper) --------------------------------

def _noise_v1():
    return {
        "id": "20260919T003427Z_control_000",
        "timestamp": "2026-09-19T00:34:27+00:00",
        "device": "hackrf",
        "protocol": "noise",
        "purpose": "signature_capture",
        "center_freq_hz": 2440000000.0,
        "sample_rate_sps": 20000000.0,
        "format": "cs8",
        "duration_s": 3.0,
        "channels": [
            {"path": "data/signature_capture/s/control_000.cs8",
             "sha256": "cd" * 32}
        ],
        "antenna": "dual-band 2.4/5.8 SMA",
        "operator_notes": "",
        "band": "control",
    }


def _elrs_v1():
    return {
        "id": "20260919T004638Z_elrs-bench_000",
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
        "operator_notes": "Boxer only, drone OFF, bench ~1m",
        "band": "2.4 GHz ISM",
        "tx_power_dbm": 20.0,
        "gain": {"mode": "manual", "lna_db": 16, "vga_db": 16},
    }


def test_migration_noise_rules():
    e = migration.migrate_entry(_noise_v1())
    assert e["band"] == "2.4 GHz ISM"      # physical band, not a class label
    assert e["label"] == "background"
    assert e["condition"] == "tx_off"
    assert e["distance_m"] is None
    assert e["los"] is None
    assert e["environment"] == "indoor_bench"
    assert e["contributor"] == "juanmicl"
    assert e["source"] == "own_capture"
    assert e["consent"] == "private"
    assert e["site_anonymized"] is True
    # ids/paths/checksums unchanged
    assert e["id"] == "20260919T003427Z_control_000"
    assert e["channels"][0]["path"] == "data/signature_capture/s/control_000.cs8"
    assert e["channels"][0]["sha256"] == "cd" * 32
    manifest_mod.validate_entry(e)


def test_migration_elrs_rules():
    e = migration.migrate_entry(_elrs_v1())
    assert e["label"] == "elrs"
    assert e["condition"] == "bench"
    assert e["distance_m"] == 1.0          # operator notes say "bench ~1m"
    assert e["los"] is True
    assert e["elrs_profile"] is None       # was not logged; not fabricated
    assert e["band"] == "2.4 GHz ISM"
    assert e["id"] == "20260919T004638Z_elrs-bench_000"
    assert e["channels"][0]["sha256"] == "ab" * 32
    manifest_mod.validate_entry(e)


def test_migration_unknown_protocol_fails_loudly():
    _must_raise(lambda: migration.migrate_entry(
        {**_elrs_v1(), "id": "x", "protocol": "dji_o4"}))


def test_migration_idempotent():
    doc = {"schema_version": "2.0",
           "recordings": [migration.migrate_entry(_noise_v1()),
                          migration.migrate_entry(_elrs_v1())]}
    assert migration.migrate_manifest(doc) == doc          # already v2: no-op
    once = migration.migrate_manifest([_noise_v1(), _elrs_v1()])
    assert migration.migrate_manifest(once) == once        # twice == once
    # an entry already at 2.0 inside a migration is returned unchanged
    v2 = migration.migrate_entry(once["recordings"][0])
    assert v2 == once["recordings"][0]


# --- Golden file: migration output of the real manifest ---------------------

def test_golden_validates_and_ids_unique():
    doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert doc["schema_version"] == "2.0"
    ids = [e["id"] for e in doc["recordings"]]
    assert len(ids) == len(set(ids)), "duplicate ids in golden manifest"
    for e in doc["recordings"]:
        manifest_mod.validate_entry(e)


def test_golden_reachable_from_v1_fixture():
    # strip v2-only fields from the golden entries -> v1 flat list ->
    # migrate -> must reproduce the golden document exactly (key-order
    # normalized).
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    fixture = []
    for e in golden["recordings"]:
        fixture.append({k: v for k, v in e.items() if k not in V2_ONLY_FIELDS})
    migrated = migration.migrate_manifest(fixture)
    canon = lambda d: json.dumps(d, sort_keys=True)  # noqa: E731
    assert canon(migrated) == canon(golden)


# --- Wrapper support in the loader ------------------------------------------

def test_append_creates_wrapper_and_query_reads_it():
    with tempfile.TemporaryDirectory() as tmp:
        mpath = os.path.join(tmp, "manifest.json")
        manifest_mod.append_entry(_v2_entry(id="a"), manifest_path=mpath)
        manifest_mod.append_entry(_v2_entry(id="b"), manifest_path=mpath)
        doc = json.loads(Path(mpath).read_text(encoding="utf-8"))
        assert doc["schema_version"] == "2.0"
        assert [e["id"] for e in doc["recordings"]] == ["a", "b"]
        assert [e["id"] for e in
                manifest_mod.query(manifest_path=mpath)] == ["a", "b"]
        assert [e["id"] for e in manifest_mod.query(
            manifest_path=mpath, label="elrs")] == ["a", "b"]
        _must_raise(lambda: manifest_mod.append_entry(
            _v2_entry(id="a"), manifest_path=mpath))


def test_append_rejects_v1_flat_manifest():
    with tempfile.TemporaryDirectory() as tmp:
        mpath = os.path.join(tmp, "manifest.json")
        Path(mpath).write_text(json.dumps([_elrs_v1()]), encoding="utf-8")
        _must_raise(lambda: manifest_mod.append_entry(
            _v2_entry(id="c"), manifest_path=mpath))


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
