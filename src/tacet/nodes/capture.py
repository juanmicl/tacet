"""Bench capture CLI: wraps hackrf_transfer (RECEIVE ONLY, -r).

Never transmits: the HackRF only records. Scenarios preset center
frequencies, gains and manifest context for the P0 bench sessions.
"""

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tacet.loaders import manifest as manifest_mod
from tacet.loaders.migration import band_for_freq_hz

FS = 20_000_000  # Msps, bytes/s = 2 * FS (cs8)
BYTES_PER_SAMPLE = 2

SCAN_BANDS_MHZ = [(5_170, 5_250), (5_750, 5_850)]
SCAN_STEP_MHZ = 10
SCAN_SETTLE_S = 0.1  # discarded after each retune (PLL/AGC settling)
ELRS_DEFAULT_MHZ = 2_440

PROTOCOL_BY_SCENARIO = {
    "o4-scan": "dji_o4",
    "o4-fixed": "dji_o4",
    "elrs-bench": "elrs",
    "control": "noise",
}


def band_for_freq_mhz(mhz: float) -> str:
    """Physical band for a center frequency (MHz); never a class label."""
    return band_for_freq_hz(float(mhz) * 1e6)


SCENARIOS = {
    "o4-scan": "scan the 5.1/5.8 GHz bands for DJI O4 video bursts",
    "o4-fixed": "record one fixed DJI O4 center frequency",
    "elrs-bench": "bench capture of the ELRS control link (Boxer TX)",
    "control": "TX-OFF control capture (protocol noise) for the session",
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


def validate_freq_mhz(freq_mhz: float | None) -> None:
    if freq_mhz is None:
        return
    if freq_mhz != int(freq_mhz) or not 100 <= freq_mhz <= 6000:
        raise ValueError(
            f"--freq-mhz must be an integer in 100-6000, got {freq_mhz}")


def validate_power(power_mw: float | None) -> None:
    if power_mw is None:
        return
    if power_mw <= 0:
        raise ValueError(f"--power must be > 0 mW, got {power_mw}")


def build_center_hz(scenario: str, ns) -> float:
    """Center frequency (MHz) for a scenario from parsed args.

    elrs-bench defaults to ELRS_DEFAULT_MHZ; o4-fixed and control require
    --freq-mhz.
    """
    if ns.freq_mhz is not None:
        return float(ns.freq_mhz)
    if scenario == "elrs-bench":
        return float(ELRS_DEFAULT_MHZ)
    raise ValueError(f"--freq-mhz is required for {scenario}")


def build_argv(center_hz: int, n_samples: int, lna: int, vga: int, path: str):
    return [
        "hackrf_transfer", "-r", str(path),
        "-f", str(int(center_hz)), "-s", str(FS),
        "-l", str(int(lna)), "-g", str(int(vga)), "-n", str(int(n_samples)),
    ]


def build_entry(args: dict) -> dict:
    session = args.get("session")
    if session is not None:
        # Session-scoped id: the UTC session directory name is unique, so
        # consecutive bench sessions cannot collide on the same scenario
        # (a bare path stem would).
        id_ = f"{session}_{args['scenario']}_{int(args.get('index', 0)):03d}"
    else:
        id_ = Path(args["path"]).stem
    entry = {
        "id": id_,
        "schema_version": "2.0",
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
        "band": band_for_freq_mhz(float(args["freq_mhz"])),
        "label": args["label"],
        "condition": args["condition"],
        "distance_m": args.get("distance_m"),   # required-nullable: explicit
        "environment": args.get("environment", "indoor_bench"),
        "los": args.get("los"),                 # required-nullable: explicit
        "contributor": args.get("contributor", "juanmicl"),
        "source": args.get("source", "own_capture"),
        "consent": args.get("consent", "private"),
        "site_anonymized": args.get("site_anonymized", True),
        "gain": {"mode": "manual", "lna_db": args["lna"], "vga_db": args["vga"]},
    }
    if args.get("elrs_profile") is not None:
        entry["elrs_profile"] = args["elrs_profile"]
    if args.get("power_mw"):
        entry["tx_power_dbm"] = float(10.0 * math.log10(args["power_mw"]))
    return entry


def _run_hackrf(argv, log_path):
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT)
    return proc


def _log_tail(log_path: Path, lines: int = 10) -> None:
    """Dump the last lines of the sidecar log to stderr (failure forensics)."""
    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            tail = fh.readlines()[-lines:]
    except OSError:
        return
    sys.stderr.write("".join(tail))


def _prune_empty_dirs(session_dir: Path) -> None:
    """Remove now-empty session dirs up to but not including data/."""
    d = session_dir
    while d.name != "data" and d != d.parent:
        try:
            d.rmdir()  # succeeds only when the directory is empty
        except OSError:
            break
        d = d.parent


def _discard_partial(path: Path, session_dir: Path) -> None:
    """Delete the partial capture + sidecar; prune empty session dirs."""
    path.unlink(missing_ok=True)          # zero orphan data
    path.with_suffix(".log").unlink(missing_ok=True)
    _prune_empty_dirs(session_dir)        # nothing left under data/


def _capture_once(ns, session_dir: Path, index: int):
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path = session_dir / f"{ns.scenario}_{index:03d}.cs8"
    n_samples = int(round(ns.duration_s * FS))
    argv = build_argv(ns.freq_mhz * 1_000_000, n_samples, ns.lna, ns.vga, path)
    try:
        proc = _run_hackrf(argv, path.with_suffix(".log"))
    except KeyboardInterrupt:
        _discard_partial(path, session_dir)
        print("interrupted; partial capture removed", file=sys.stderr)
        raise
    if proc.returncode != 0:
        _log_tail(path.with_suffix(".log"))   # only record of why it failed
        _discard_partial(path, session_dir)
        print(f"hackrf_transfer failed (rc={proc.returncode}); "
              f"partial file removed", file=sys.stderr)
        return None
    expected = BYTES_PER_SAMPLE * n_samples
    actual = path.stat().st_size
    warn = ""
    if actual != expected:
        warn = (f" [byte-count warning: expected {expected}, got {actual} "
                f"— possible dropped samples, see sidecar log]")
        print(f"WARNING:{warn}", file=sys.stderr)
    entry = build_entry(dict(
        scenario=ns.scenario, freq_mhz=ns.freq_mhz, duration_s=ns.duration_s,
        lna=ns.lna, vga=ns.vga, power_mw=ns.power, notes=ns.notes + warn,
        distance_m=ns.distance_m, environment=ns.environment,
        los=ns.los, label=ns.label, condition=ns.condition,
        elrs_profile=ns.elrs_profile, contributor=ns.contributor,
        antenna=ns.antenna, path=str(path),
        session=session_dir.name, index=index,
        sha256=manifest_mod.compute_sha256(path), timestamp=ts))
    manifest_mod.append_entry(entry)
    print(f"captured {path} ({actual} bytes){warn}")
    return entry


def o4_scan(ns, session_dir: Path):
    """Dwell-scan the O4 bins; return the winning MHz (None if none).

    Per bin: a short capture that is discarded afterwards, then the
    DC-masked Welch peak over the loaded dwell file decides the winner.
    Appends winner and peak to ns.notes.
    """
    from tacet.dsp import features
    from tacet.loaders.cs8 import load_cs8

    best_mhz = None
    best_peak = -math.inf
    n_samples = max(int(round(ns.dwell_s * FS)), 1)
    for mhz in o4_scan_bins():
        dwell = session_dir / f"scan_{mhz}MHz.cs8"
        argv = build_argv(mhz * 1_000_000, n_samples, ns.lna, ns.vga, dwell)
        try:
            proc = _run_hackrf(argv, dwell.with_suffix(".log"))
        except KeyboardInterrupt:
            dwell.unlink(missing_ok=True)
            dwell.with_suffix(".log").unlink(missing_ok=True)
            raise
        if proc.returncode != 0:
            dwell.unlink(missing_ok=True)
            dwell.with_suffix(".log").unlink(missing_ok=True)
            continue
        z = load_cs8(str(dwell))
        dwell.unlink(missing_ok=True)   # discard the dwell capture
        dwell.with_suffix(".log").unlink(missing_ok=True)
        z = z[int(SCAN_SETTLE_S * FS):]  # settle discard after the retune
        if z.size == 0:
            continue
        _, p = features.welch_psd(z, float(FS))
        mask = features._dc_mask(p.size)
        peak = float(p[mask].max()) if mask.any() else float(p.max())
        if peak > best_peak:
            best_mhz, best_peak = mhz, peak
    if best_mhz is not None:
        note = f"[o4-scan winner {best_mhz} MHz, peak {best_peak:.4g}]"
        ns.notes = (ns.notes + " " + note).strip()
    return best_mhz


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
        for name, desc in SCENARIOS.items():
            print(f"{name}: {desc}")
        return 0
    if args.scenario is None:
        parser.error("a scenario is required (see --list)")

    # Validation first.
    try:
        validate_gains(args.lna, args.vga)
        validate_duration(args.duration_s)
        validate_freq_mhz(args.freq_mhz)
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
            args.freq_mhz = build_center_hz(args.scenario, args)
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
            bins = o4_scan_bins()
            print(f"o4-scan plan: {len(bins)} bins, dwell {args.dwell_s} s")
            print("bins (MHz): " + " ".join(str(b) for b in bins))
            return 0
        try:
            freq_mhz = build_center_hz(args.scenario, args)
            band_for_freq_mhz(freq_mhz)  # unmapped freqs fail before output
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        n_samples = int(round(args.duration_s * FS))
        session_name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        rel_path = (f"data/signature_capture/{session_name}/"
                    f"{args.scenario}_000.cs8")
        entry = build_entry(dict(
            scenario=args.scenario, freq_mhz=freq_mhz,
            duration_s=args.duration_s, lna=args.lna, vga=args.vga,
            power_mw=args.power, notes=args.notes,
            distance_m=args.distance_m, environment=args.environment,
            los=args.los, label=args.label, condition=args.condition,
            elrs_profile=args.elrs_profile, contributor=args.contributor,
            antenna=args.antenna, path=rel_path,
            session=session_name, index=0, sha256="(dry-run)",
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ))
        print(f"repo root: {root}")
        print(" ".join(build_argv(
            int(freq_mhz * 1e6), n_samples, args.lna, args.vga, rel_path)))
        print(json.dumps(entry, indent=2))
        return 0

    # Real capture path.
    if shutil.which("hackrf_transfer") is None:
        print("hackrf_transfer not found on PATH. "
              "Install hackrf tools (e.g. apt install hackrf).", file=sys.stderr)
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
            winner = o4_scan(args, session_dir)
            if winner is None:
                _prune_empty_dirs(session_dir)
                print("o4-scan: no bin produced a dwell capture",
                      file=sys.stderr)
                return 2
            args.freq_mhz = float(winner)
        entry = _capture_once(args, session_dir, 0)
    except KeyboardInterrupt:
        # _capture_once/_o4 scan already cleaned their partial files;
        # prune any empty session dirs left behind.
        _prune_empty_dirs(session_dir)
        print("interrupted", file=sys.stderr)
        return 130
    return 0 if entry is not None else 2
