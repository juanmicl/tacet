"""Bench capture CLI: wraps hackrf_transfer (RECEIVE ONLY, -r).

Never transmits: the HackRF only records. Scenarios preset center
frequencies, gains and manifest context for the P0 bench sessions.
"""

import argparse
import json
import math
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tacet.loaders import manifest as manifest_mod

FS = 20_000_000  # Msps, bytes/s = 2 * FS (cs8)
BYTES_PER_SAMPLE = 2

SCAN_BANDS_MHZ = [(5_170, 5_250), (5_750, 5_850)]
SCAN_STEP_MHZ = 10
ELRS_DEFAULT_MHZ = 2_440

PROTOCOL_BY_SCENARIO = {
    "o4-scan": "dji_o4",
    "o4-fixed": "dji_o4",
    "elrs-bench": "elrs",
}
BAND_BY_SCENARIO = {
    "o4-scan": "5.1/5.8 GHz",
    "o4-fixed": "5.1/5.8 GHz",
    "elrs-bench": "2.4 GHz ISM",
}

SCENARIOS = {
    "o4-scan": "scan the 5.1/5.8 GHz bands for DJI O4 video bursts",
    "o4-fixed": "record one fixed DJI O4 center frequency",
    "elrs-bench": "bench capture of the ELRS control link (Boxer TX)",
}


def o4_scan_bins():
    bins = []
    for lo, hi in SCAN_BANDS_MHZ:
        bins.extend(range(lo, hi + 1, SCAN_STEP_MHZ))
    return bins


def validate_gains(lna: int, vga: int) -> None:
    if lna % 8 != 0 or not 0 <= lna <= 40:
        raise ValueError(f"--lna must be 0-40 in steps of 8, got {lna}")
    if vga % 2 != 0 or not 0 <= vga <= 62:
        raise ValueError(f"--vga must be 0-62 in steps of 2, got {vga}")


def validate_duration(duration_s: float) -> None:
    if not 1.0 <= duration_s <= 60.0:
        raise ValueError(f"--duration-s must be 1-60, got {duration_s}")


def build_argv(center_hz: int, n_samples: int, lna: int, vga: int, path: str):
    return [
        "hackrf_transfer", "-r", str(path),
        "-f", str(int(center_hz)), "-s", str(FS),
        "-l", str(int(lna)), "-g", str(int(vga)), "-n", str(int(n_samples)),
    ]


def build_entry(args: dict) -> dict:
    return {
        "id": Path(args["path"]).stem,
        "timestamp": args["timestamp"],
        "device": "hackrf",
        "protocol": PROTOCOL_BY_SCENARIO[args["scenario"]],
        "purpose": "signature_capture",
        "center_freq_hz": float(args["freq_mhz"] * 1e6),
        "sample_rate_sps": float(FS),
        "format": "cs8",
        "duration_s": float(args["duration_s"]),
        "channels": [{"path": args["path"], "sha256": args["sha256"]}],
        "antenna": args["antenna"],
        "operator_notes": args["notes"],
        "band": BAND_BY_SCENARIO[args["scenario"]],
        "tx_power_dbm": float(10.0 * math.log10(args["power_mw"]))
        if args.get("power_mw") else None,
        "distance_m": args.get("distance_m"),
        "environment": args.get("env"),
        "los_nlos": args.get("los"),
        "gain": {"mode": "manual", "lna_db": args["lna"], "vga_db": args["vga"]},
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="tacet capture",
        description="Bench capture scenarios (RECEIVE ONLY: hackrf_transfer -r).",
    )
    parser.add_argument(
        "scenario", nargs="?", choices=sorted(SCENARIOS),
        help="capture scenario (see --list)",
    )
    parser.add_argument(
        "--freq-mhz", type=float, default=None,
        help="center frequency in MHz (required for o4-fixed; "
             "default 2440 for elrs-bench)",
    )
    parser.add_argument("--duration-s", type=float, default=5.0)
    parser.add_argument("--lna", type=int, default=32)
    parser.add_argument("--vga", type=int, default=32)
    parser.add_argument(
        "--power", type=float, default=None,
        help="known TX power in mW (elrs-bench only)",
    )
    parser.add_argument("--notes", default="")
    parser.add_argument("--distance-m", type=float, default=None)
    parser.add_argument("--env", default=None, choices=["urban", "rural", "open"])
    parser.add_argument("--los", default=None, choices=["los", "nlos"])
    parser.add_argument("--antenna", default="")
    parser.add_argument(
        "--dwell-s", type=float, default=0.5,
        help="seconds per bin for o4-scan",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print the argv and manifest entry; touch nothing",
    )
    parser.add_argument(
        "--repo-root", default=None,
        help="repo root anchor (default: nearest ancestor with pyproject.toml)",
    )
    parser.add_argument("--list", action="store_true", help="list scenarios")

    args = parser.parse_args(argv)

    if args.list:
        for name, desc in SCENARIOS.items():
            print(f"{name}: {desc}")
        return 0
    if args.scenario is None:
        parser.error("a scenario is required (see --list)")

    # Validation first.
    try:
        validate_gains(args.lna, args.vga)
        validate_duration(args.duration_s)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.power is not None and args.scenario != "elrs-bench":
        print("error: --power applies to elrs-bench only", file=sys.stderr)
        return 2

    freq_mhz = args.freq_mhz
    if freq_mhz is None:
        if args.scenario == "elrs-bench":
            freq_mhz = float(ELRS_DEFAULT_MHZ)
        elif args.scenario == "o4-fixed":
            print("error: --freq-mhz is required for o4-fixed", file=sys.stderr)
            return 2

    root = Path(manifest_mod._repo_root(args.repo_root))
    rel_path = f"data/signature_capture/{args.scenario}/{args.scenario}_000.cs8"

    if args.dry_run:
        if args.scenario == "o4-scan" and freq_mhz is None:
            bins = o4_scan_bins()
            print(f"o4-scan plan: {len(bins)} bins, dwell {args.dwell_s} s")
            print("bins (MHz): " + " ".join(str(b) for b in bins))
            return 0
        n_samples = int(round(args.duration_s * FS))
        entry = build_entry(dict(
            scenario=args.scenario, freq_mhz=freq_mhz,
            duration_s=args.duration_s, lna=args.lna, vga=args.vga,
            power_mw=args.power, notes=args.notes,
            distance_m=args.distance_m, env=args.env, los=args.los,
            antenna=args.antenna, path=rel_path, sha256="(dry-run)",
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ))
        print(f"repo root: {root}")
        print(" ".join(build_argv(
            int(freq_mhz * 1e6), n_samples, args.lna, args.vga, rel_path)))
        print(json.dumps(entry, indent=2))
        return 0

    print("capture execution not implemented yet (Task 6)", file=sys.stderr)
    return 2
