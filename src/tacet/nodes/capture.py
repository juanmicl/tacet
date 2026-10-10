"""Bench capture CLI: device-agnostic front-end (RECEIVE ONLY).

Never transmits: every device backend only records. This module owns the
session hygiene — argparse (including --device), scenario x device
resolution, session dirs and timestamps, the disk 2x check, dry-run,
manifest append, and zero-orphan pruning — and assembles the full v2
manifest entry around per-backend capture parts (assemble_entry).
Device drivers live in tacet.nodes.backends (hackrf today; pluto lands
with the Pluto+ bring-up).
"""

import argparse
import json
import math
import os
import shutil
import subprocess  # retained: tests patch capture.subprocess (module object)
import sys
from datetime import datetime, timezone
from pathlib import Path

from tacet.loaders import manifest as manifest_mod
from tacet.nodes.backends import hackrf

# --- Re-exports: symbols moved to the hackrf backend ------------------------
# Kept importable (and patchable) as capture.<symbol> because the existing
# test suite and any external scripts reference them here.
FS = hackrf.FS
BYTES_PER_SAMPLE = hackrf.BYTES_PER_SAMPLE
SCAN_BANDS_MHZ = hackrf.SCAN_BANDS_MHZ
SCAN_STEP_MHZ = hackrf.SCAN_STEP_MHZ
SCAN_SETTLE_S = hackrf.SCAN_SETTLE_S
ELRS_DEFAULT_MHZ = hackrf.ELRS_DEFAULT_MHZ
PROTOCOL_BY_SCENARIO = hackrf.PROTOCOL_BY_SCENARIO
SCENARIOS = hackrf.SCENARIOS
band_for_freq_mhz = hackrf.band_for_freq_mhz
o4_scan_bins = hackrf.o4_scan_bins
validate_gains = hackrf.validate_gains
validate_duration = hackrf.validate_duration
validate_freq_mhz = hackrf.validate_freq_mhz
build_argv = hackrf.build_argv
build_entry = hackrf.build_entry

# Registered device backends; the CLI scenario menu is the union of their
# SCENARIOS registries. The Pluto+ backend registers here in its task.
BACKENDS = {"hackrf": hackrf}


def validate_power(power_mw: float | None) -> None:
    if power_mw is None:
        return
    if power_mw <= 0:
        raise ValueError(f"--power must be > 0 mW, got {power_mw}")


def assemble_entry(parts: dict, ns, session_dir: Path, index: int) -> dict:
    """Assemble the full v2 entry around backend capture parts.

    Front-end-owned fields are built here: the session-scoped id scheme
    ({session}_{scenario}_{index:03d}), the UTC timestamp, the session
    dir, duration, and governance defaults. ``parts`` carries the
    backend fragments: device, protocol, purpose, center frequency,
    sample rate, format, band, channels (+sha256), gain, and extras.
    """
    entry = {
        "id": f"{session_dir.name}_{ns.scenario}_{int(index):03d}",
        "schema_version": "2.0",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "device": parts["device"],
        "protocol": parts["protocol"],
        "purpose": parts["purpose"],
        "center_freq_hz": parts["center_freq_hz"],
        "sample_rate_sps": parts["sample_rate_sps"],
        "format": parts["format"],
        "duration_s": float(ns.duration_s),
        "channels": parts["channels"],
        "antenna": ns.antenna,
        "operator_notes": ns.notes + parts.get("notes_warn", ""),
        "band": parts["band"],
        "label": ns.label,
        "condition": ns.condition,
        "distance_m": ns.distance_m,            # required-nullable: explicit
        "environment": ns.environment,
        "los": ns.los,                          # required-nullable: explicit
        "contributor": ns.contributor,
        "source": parts.get("source", "own_capture"),
        "consent": parts.get("consent", "private"),
        "site_anonymized": parts.get("site_anonymized", True),
        "gain": parts["gain"],
    }
    if getattr(ns, "elrs_profile", None) is not None:
        entry["elrs_profile"] = ns.elrs_profile
    if getattr(ns, "power", None):
        entry["tx_power_dbm"] = float(10.0 * math.log10(ns.power))
    return entry


def _prune_empty_dirs(session_dir: Path) -> None:
    """Remove now-empty session dirs up to but not including data/."""
    d = session_dir
    while d.name != "data" and d != d.parent:
        try:
            d.rmdir()  # succeeds only when the directory is empty
        except OSError:
            break
        d = d.parent


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="tacet capture",
        description="Bench capture scenarios (RECEIVE ONLY: backends "
                    "record, never transmit).",
    )
    parser.add_argument(
        "scenario", nargs="?", choices=sorted(SCENARIOS),
        help="capture scenario (see --list)",
    )
    parser.add_argument(
        "--device", default="hackrf", choices=sorted(BACKENDS),
        help="capture device backend (default: hackrf)",
    )
    parser.add_argument(
        "--freq-mhz", type=float, default=None,
        help="center frequency in MHz (required for o4-fixed/control; "
             "default 2440 for elrs-bench)",
    )
    parser.add_argument("--duration-s", type=float, default=5.0)
    parser.add_argument("--lna", type=int, default=32)
    parser.add_argument("--vga", type=int, default=32)
    parser.add_argument(
        "--label", required=True,
        choices=["elrs", "dji_o4", "analog_fpv", "background", "interference"],
        help="ML signal class in the recording (required, manifest v2)")
    parser.add_argument(
        "--condition", required=True,
        choices=["bench", "flying", "walk", "tx_off"],
        help="experimental condition (required, manifest v2)")
    parser.add_argument(
        "--environment", default="indoor_bench",
        choices=["indoor_bench", "rural", "urban", "open_field"])
    parser.add_argument("--distance-m", type=float, default=None)
    parser.add_argument(
        "--los", action=argparse.BooleanOptionalAction, default=None,
        help="line of sight; omit for null (not meaningful)")
    parser.add_argument(
        "--elrs-profile", default=None,
        help="ELRS packet rate / mode (e.g. D500); expected on ELRS captures")
    parser.add_argument("--contributor", default="juanmicl")
    parser.add_argument(
        "--power", type=float, default=None,
        help="known TX power in mW (elrs-bench only)",
    )
    parser.add_argument("--notes", default="")
    parser.add_argument("--antenna", default="dual-band 2.4/5.8 SMA")
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
        for dev_name, dev in sorted(BACKENDS.items()):
            for scenario, desc in dev.SCENARIOS.items():
                print(f"{scenario}: {desc}")
        return 0
    if args.scenario is None:
        parser.error("a scenario is required (see --list)")

    # Scenario x device resolution: reject unsupported pairs.
    backend = BACKENDS[args.device]
    if args.scenario not in backend.SCENARIOS:
        print(f"error: scenario '{args.scenario}' is not supported by "
              f"device '{args.device}'", file=sys.stderr)
        return 2

    # Validation first.
    try:
        backend.validate(args)
        validate_power(args.power)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.power is not None and args.scenario != "elrs-bench":
        print("error: --power applies to elrs-bench only", file=sys.stderr)
        return 2
    if args.scenario != "o4-scan":
        # o4-scan resolves its center from the dwell scan below.
        try:
            args.freq_mhz = backend.build_center_hz(args.scenario, args)
            # Unmapped frequencies must fail BEFORE any hardware run,
            # session directory or dry-run output (zero-orphan invariant).
            band_for_freq_mhz(args.freq_mhz)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    root = Path(manifest_mod._repo_root(args.repo_root))
    os.chdir(root)  # anchor the relative session paths below (any cwd)
    if args.scenario == "elrs-bench" and args.elrs_profile is None:
        print("warning: --elrs-profile not given; log the ELRS packet "
              "rate/mode (e.g. D500) so captures stay comparable",
              file=sys.stderr)
    if args.dry_run:
        if args.scenario == "o4-scan" and args.freq_mhz is None:
            bins = backend.o4_scan_bins()
            print(f"o4-scan plan: {len(bins)} bins, dwell {args.dwell_s} s")
            print("bins (MHz): " + " ".join(str(b) for b in bins))
            return 0
        try:
            freq_mhz = backend.build_center_hz(args.scenario, args)
            band_for_freq_mhz(freq_mhz)  # unmapped freqs fail before output
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        n_samples = int(round(args.duration_s * FS))
        session_dir = (
            Path("data") / "signature_capture"
            / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        rel_path = str(session_dir / f"{args.scenario}_000.cs8")
        parts = backend.entry_parts(args, rel_path, sha256="(dry-run)")
        entry = assemble_entry(parts, args, session_dir, 0)
        print(f"repo root: {root}")
        print(" ".join(build_argv(
            int(freq_mhz * 1e6), n_samples, args.lna, args.vga, rel_path)))
        print(json.dumps(entry, indent=2))
        return 0

    # Real capture path.
    try:
        backend.check_available()
    except (RuntimeError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2
    est_bytes = BYTES_PER_SAMPLE * FS * args.duration_s
    free = shutil.disk_usage(str(root)).free
    if free < 2 * est_bytes:
        print(f"insufficient disk: need ~{est_bytes/1e6:.0f} MB, "
              f"free {free/1e6:.0f} MB", file=sys.stderr)
        return 2
    session_dir = (
        Path("data") / "signature_capture"
        / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    session_dir.mkdir(parents=True, exist_ok=True)
    try:
        if args.scenario == "o4-scan":
            winner = backend.o4_scan(args, session_dir)
            if winner is None:
                _prune_empty_dirs(session_dir)
                print("o4-scan: no bin produced a dwell capture",
                      file=sys.stderr)
                return 2
            args.freq_mhz = float(winner)
        parts = backend.capture_once(args, session_dir, 0)
        if parts is None:
            _prune_empty_dirs(session_dir)  # backend removed its files
            return 2
        entry = assemble_entry(parts, args, session_dir, 0)
        manifest_mod.append_entry(entry)
        print(f"captured {parts['channels'][0]['path']} "
              f"({parts['size_bytes']} bytes){parts['notes_warn']}")
    except KeyboardInterrupt:
        # backends already removed their partial files; prune empty dirs
        _prune_empty_dirs(session_dir)
        print("interrupted", file=sys.stderr)
        return 130
    return 0
