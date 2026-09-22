"""Tests for the DroneDetect V2 dataset index (parser, provenance, sampling)."""

import tempfile
from pathlib import Path


def test_parse_valid_names():
    from tacet.loaders import dronedetect as dd

    m = dd.parse_dronedetect_filename("MIN_0000_00.dat")
    assert m["drone"] == "MIN"
    assert m["model"] == "DJI Mavic Mini"
    assert m["mode"] == "ON"
    assert m["interference"] == "clean"
    assert m["file_num"] == 0
    m2 = dd.parse_dronedetect_filename("AIR_0010_03.dat")
    assert m2["drone"] == "AIR"
    assert m2["mode"] == "FY"
    assert m2["file_num"] == 3


def test_parse_prefix_aliases():
    from tacet.loaders import dronedetect as dd

    a = dd.parse_dronedetect_filename("MA1_0000_01.dat")
    assert a["prefix"] == "MA1" and a["drone"] == "MP1"
    assert a["model"] == "DJI Mavic Pro"
    b = dd.parse_dronedetect_filename("MAV_0001_02.dat")
    assert b["prefix"] == "MAV" and b["drone"] == "MP2"
    assert b["model"] == "DJI Mavic 2 Pro"


def test_parse_rejects_invalid():
    from tacet.loaders import dronedetect as dd

    for bad in (
        "BEB_0000_00.dat",   # unknown prefix (V1 roster, not in V2)
        "MIN_0011_00.dat",   # invalid mode
        "MIN_0500_00.dat",   # invalid interference code
        "MIN_0000_00.txt",   # wrong extension
        "MIN0000_00.dat",    # malformed structure
    ):
        try:
            dd.parse_dronedetect_filename(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")


def _make_tree(root):
    """CLEAN-like tree: aliased MA1 files inside MP1_ON, plus MIN dirs."""
    for rel in (
        "MP1_ON/MA1_0000_00.dat",
        "MP1_ON/MA1_0000_01.dat",
        "MIN_ON/MIN_0000_00.dat",
        "MIN_FY/MIN_0010_00.dat",
    ):
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")


def test_list_files_provenance_and_alias():
    from tacet.loaders import dronedetect as dd

    with tempfile.TemporaryDirectory() as td:
        _make_tree(td)
        files = dd.list_dronedetect_files(td)
    assert len(files) == 4, f"got {len(files)}"
    mp1 = [f for f in files if f["drone"] == "MP1"]
    assert len(mp1) == 2
    assert all(f["model"] == "DJI Mavic Pro" for f in mp1)
    assert all(f["mode"] == "ON" for f in mp1)
    for f in files:
        assert f["source"] == "public_dataset"
        assert f["doi"] == dd.DATASET_DOI
        assert f["license"] == dd.DATASET_LICENSE
        assert f["sample_rate_sps"] == 60e6
        assert f["center_freq_hz"] == 2.4375e9
        assert f["duration_s"] == 2.0
        assert f["interference"] == "clean"
        assert isinstance(f["path"], str)
        assert isinstance(f["file_num"], int)


def test_list_files_dir_file_mismatch_raises():
    from tacet.loaders import dronedetect as dd

    with tempfile.TemporaryDirectory() as td:
        _make_tree(td)
        (Path(td) / "MIN_ON" / "AIR_0000_00.dat").write_bytes(b"")
        try:
            dd.list_dronedetect_files(td)
        except ValueError:
            return
    raise AssertionError("expected ValueError on directory/file mismatch")


def test_list_files_rejects_unexpected_directory():
    from tacet.loaders import dronedetect as dd

    with tempfile.TemporaryDirectory() as td:
        _make_tree(td)
        (Path(td) / "NOPE_AB").mkdir()
        try:
            dd.list_dronedetect_files(td)
        except ValueError:
            return
    raise AssertionError("expected ValueError for unexpected directory name")


def test_list_files_missing_root_returns_empty():
    from tacet.loaders import dronedetect as dd

    with tempfile.TemporaryDirectory() as td:
        assert dd.list_dronedetect_files(str(Path(td) / "nope")) == []


def test_expected_interference_accepts_matching():
    from tacet.loaders import dronedetect as dd

    with tempfile.TemporaryDirectory() as td:
        _make_tree(td)
        files = dd.list_dronedetect_files(td, expected_interference="clean")
    assert len(files) == 4, f"got {len(files)}"
    assert all(f["interference"] == "clean" for f in files)


def test_expected_interference_rejects_mismatch():
    from tacet.loaders import dronedetect as dd

    with tempfile.TemporaryDirectory() as td:
        _make_tree(td)
        (Path(td) / "MIN_ON" / "MIN_0100_00.dat").write_bytes(b"")
        try:
            dd.list_dronedetect_files(td, expected_interference="clean")
        except ValueError as exc:
            assert "MIN_0100_00.dat" in str(exc), f"file not named: {exc}"
            assert "bluetooth" in str(exc), f"actual not named: {exc}"
            assert "'clean'" in str(exc), f"expected not named: {exc}"
            return
    raise AssertionError("expected ValueError on interference mismatch")


def _desc(drone, mode, num):
    return {
        "path": f"data/x/{drone}_{mode}/{drone}_0000_{num:02d}.dat",
        "drone": drone,
        "mode": mode,
        "file_num": num,
    }


def test_sample_files_seeded_and_per_class():
    from tacet.loaders import dronedetect as dd

    files = [_desc("MIN", "ON", n) for n in range(3)] + [
        _desc("AIR", "FY", n) for n in range(5)
    ]
    a = dd.sample_files(files, n_per_class=2, seed=7)
    b = dd.sample_files(files, n_per_class=2, seed=7)
    assert a == b, "same seed must give identical output"
    counts = {}
    for f in a:
        key = (f["drone"], f["mode"])
        counts[key] = counts.get(key, 0) + 1
    assert counts == {("MIN", "ON"): 2, ("AIR", "FY"): 2}, counts
    over = dd.sample_files(files, n_per_class=10, seed=0)
    assert len(over) == len(files), "n_per_class above class size takes all"


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
