"""DroneDetect V2 dataset indexing — filename parsing, provenance, sampling.

Dataset: Swinney & Woods, IEEE DataPort 2021, doi:10.21227/5jjj-1m32
(CC BY 4.0). CLEAN subset layout on disk:

    CLEAN/<CODE>_<MODE>/<CODE>_<II><MM>_<NN>.dat

<II> is the interference condition (00 in CLEAN), <MM> the flight mode
(00 motors-on, 01 hover, 10 flying), <NN> the file number (00-04).
Author-side prefix quirk: MP1 directories contain MA1_* files and MP2
directories contain MAV_* files; FILE_PREFIX_ALIASES normalizes both to the
directory-level canonical code. Metadata only; IQ reading goes through
tacet.loaders.cf32.
"""

import re
from pathlib import Path

import numpy as np

SAMPLE_RATE_SPS = 60e6
CENTER_FREQ_HZ = 2.4375e9
DATASET_DOI = "10.21227/5jjj-1m32"
DATASET_LICENSE = "CC-BY-4.0"

DRONE_MODELS = {
    "MIN": "DJI Mavic Mini",
    "AIR": "DJI Air 2S",
    "PHA": "DJI Phantom 4",
    "INS": "DJI Inspire 2",
    "DIS": "Parrot Disco",
    "MP1": "DJI Mavic Pro",
    "MP2": "DJI Mavic 2 Pro",
}

FILE_PREFIX_ALIASES = {"MA1": "MP1", "MAV": "MP2"}

MODES = {"00": "ON", "01": "HO", "10": "FY"}

INTERFERENCE = {"00": "clean", "01": "bluetooth", "10": "wifi", "11": "both"}

DEFAULT_ROOT = "data/datasets/dronedetect_v2/CLEAN"

_FILENAME_RE = re.compile(
    r"^(?P<prefix>[A-Z0-9]{3})_(?P<ii>\d{2})(?P<mm>\d{2})_(?P<nn>\d{2})\.dat$"
)


def parse_dronedetect_filename(name):
    """Parse a DroneDetect .dat filename into metadata.

    Returns a dict with prefix (raw file prefix), drone (canonical code),
    model, mode, interference and file_num. Raises ValueError on unknown
    prefixes (after alias normalization), invalid mode/interference codes,
    or malformed structure.
    """
    m = _FILENAME_RE.match(name)
    if m is None:
        raise ValueError(f"malformed DroneDetect filename: {name!r}")
    prefix = m.group("prefix")
    drone = FILE_PREFIX_ALIASES.get(prefix, prefix)
    if drone not in DRONE_MODELS:
        raise ValueError(f"unknown drone prefix {prefix!r} in {name!r}")
    mm = m.group("mm")
    if mm not in MODES:
        raise ValueError(
            f"invalid mode {mm!r} in {name!r} (expected one of {sorted(MODES)})"
        )
    ii = m.group("ii")
    if ii not in INTERFERENCE:
        raise ValueError(f"invalid interference code {ii!r} in {name!r}")
    return {
        "prefix": prefix,
        "drone": drone,
        "model": DRONE_MODELS[drone],
        "mode": MODES[mm],
        "interference": INTERFERENCE[ii],
        "file_num": int(m.group("nn")),
    }


def list_dronedetect_files(root=DEFAULT_ROOT):
    """Index one subset directory tree; one descriptor per .dat file.

    Descriptors carry per-file provenance (source, doi, license) plus the
    physical parameters. Directory names must be <CODE>_<MODE> and must
    agree with the contained filenames (via aliases). A missing root
    returns an empty list.
    """
    root_path = Path(root)
    if not root_path.is_dir():
        return []
    files = []
    for directory in sorted(p for p in root_path.iterdir() if p.is_dir()):
        code, _, dir_mode = directory.name.partition("_")
        if code not in DRONE_MODELS or dir_mode not in MODES.values():
            raise ValueError(
                f"unexpected directory name {directory.name!r} under {root}"
            )
        for entry in sorted(directory.glob("*.dat")):
            meta = parse_dronedetect_filename(entry.name)
            if meta["drone"] != code or meta["mode"] != dir_mode:
                raise ValueError(
                    f"directory/file mismatch: {directory.name!r} "
                    f"vs {entry.name!r}"
                )
            files.append(
                {
                    "path": str(entry),
                    "drone": meta["drone"],
                    "model": meta["model"],
                    "mode": meta["mode"],
                    "interference": meta["interference"],
                    "file_num": meta["file_num"],
                    "source": "public_dataset",
                    "doi": DATASET_DOI,
                    "license": DATASET_LICENSE,
                    "sample_rate_sps": SAMPLE_RATE_SPS,
                    "center_freq_hz": CENTER_FREQ_HZ,
                    "duration_s": 2.0,
                }
            )
    return files


def sample_files(files, n_per_class, seed=0):
    """Seeded random subset of descriptors, n_per_class per (drone, mode).

    Classes with fewer files than n_per_class contribute all their files.
    Deterministic for a fixed input and seed; output sorted by path.
    """
    rng = np.random.default_rng(seed)
    by_class = {}
    for f in files:
        by_class.setdefault((f["drone"], f["mode"]), []).append(f)
    picked = []
    for key in sorted(by_class):
        group = sorted(by_class[key], key=lambda f: f["path"])
        k = min(n_per_class, len(group))
        idx = sorted(rng.permutation(len(group))[:k])
        picked.extend(group[i] for i in idx)
    return sorted(picked, key=lambda f: f["path"])
