"""HackRF capture backend (RECEIVE ONLY: hackrf_transfer -r).

Device driver for the P0 bench scenarios. Owns config assembly, the
hackrf_transfer subprocess run with its sidecar log, per-file byte-count
checks, sha256, and the HackRF parts of the manifest entry. Session
hygiene (session dirs and timestamps, disk headroom, dry-run, manifest
append, orphan prune) lives in the front-end: tacet.nodes.capture.
Never transmits: the HackRF only records.

Contract surface consumed by the front-end (tacet.nodes.capture); Task 3
implements the same surface for pluto:

- ``SCENARIOS: dict`` — scenario name -> one-line description (the CLI
  menu is the union across registered backends).
- ``validate(args) -> None`` — device-envelope validation of the parsed
  CLI args; raises ValueError with a user-facing message.
- ``check_available() -> None`` — raises RuntimeError when the device
  tooling is unusable (message printed verbatim, exit code 2).
- ``capture_once(ns, session_dir, index) -> dict | None`` — one capture;
  returns the backend entry parts (device, protocol, purpose,
  center_freq_hz, sample_rate_sps, format, band, channels + sha256,
  gain, plus ``notes_warn``/``size_bytes`` extras). None means there is
  nothing to record: the device run failed (partial files removed) or,
  for future backends, the scenario has no capture step after its scan.
- ``scan(ns, session_dir) -> str | None`` (optional; generic alias for
  ``o4_scan``) — dwell-scan before the capture step; returns the winning
  center in MHz, or None when no bin produced a dwell capture.
- ``entry_parts(ns, path, sha256, warn="") -> dict`` — parts assembly
  WITHOUT touching hardware; the front-end's dry-run path consumes this
  to preview the entry.
"""

import math
import shutil
import subprocess
import sys
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

SCENARIOS = {
    "o4-scan": "scan the 5.1/5.8 GHz bands for DJI O4 video bursts",
    "o4-fixed": "record one fixed DJI O4 center frequency",
    "elrs-bench": "bench capture of the ELRS control link (Boxer TX)",
    "control": "TX-OFF control capture (protocol noise) for the session",
}


def band_for_freq_mhz(mhz: float) -> str:
    """Physical band for a center frequency (MHz); never a class label."""
    return band_for_freq_hz(float(mhz) * 1e6)


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


def validate(args) -> None:
    """Validate parsed CLI args against the HackRF envelopes.

    Raises ValueError with a user-facing message; the front-end turns it
    into exit code 2 before anything is touched.
    """
    validate_gains(args.lna, args.vga)
    validate_duration(args.duration_s)
    validate_freq_mhz(args.freq_mhz)


def check_available() -> None:
    """Raise RuntimeError when hackrf_transfer is not on PATH."""
    if shutil.which("hackrf_transfer") is None:
        raise RuntimeError(
            "hackrf_transfer not found on PATH. "
            "Install hackrf tools (e.g. apt install hackrf).")


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


def entry_parts(ns, path: str, sha256: str, warn: str = "") -> dict:
    """HackRF manifest-entry parts (the backend-owned fragments).

    The front-end wraps these with the session-scoped id, timestamp,
    duration, and governance defaults via capture.assemble_entry.
    ``warn`` is the byte-count warning suffix for operator_notes (empty
    on a clean capture).
    """
    freq_mhz = float(ns.freq_mhz)
    return {
        "device": "hackrf",
        "protocol": PROTOCOL_BY_SCENARIO[ns.scenario],
        "purpose": "signature_capture",
        "center_freq_hz": freq_mhz * 1e6,
        "sample_rate_sps": float(FS),
        "format": "cs8",
        "band": band_for_freq_mhz(freq_mhz),
        "channels": [{"path": path, "sha256": sha256}],
        "gain": {"mode": "manual", "lna_db": ns.lna, "vga_db": ns.vga},
        "notes_warn": warn,
    }


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


def capture_once(ns, session_dir: Path, index: int) -> dict | None:
    """Run one hackrf_transfer capture; return manifest entry parts.

    Backend-owned: argv assembly, the subprocess run with its sidecar
    log, the per-file byte-count check + warning, and sha256. Returns
    None when hackrf_transfer failed (partial file removed; the
    front-end owns the session-dir prune and the exit code).
    KeyboardInterrupt removes the partial capture and re-raises.
    """
    path = session_dir / f"{ns.scenario}_{index:03d}.cs8"
    n_samples = int(round(ns.duration_s * FS))
    argv = build_argv(ns.freq_mhz * 1_000_000, n_samples, ns.lna, ns.vga, path)
    try:
        proc = _run_hackrf(argv, path.with_suffix(".log"))
    except KeyboardInterrupt:
        path.unlink(missing_ok=True)          # zero orphan data
        path.with_suffix(".log").unlink(missing_ok=True)
        print("interrupted; partial capture removed", file=sys.stderr)
        raise
    if proc.returncode != 0:
        _log_tail(path.with_suffix(".log"))   # only record of why it failed
        path.unlink(missing_ok=True)
        path.with_suffix(".log").unlink(missing_ok=True)
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
    parts = entry_parts(ns, str(path), manifest_mod.compute_sha256(path),
                        warn=warn)
    parts["size_bytes"] = actual
    return parts


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


# Generic contract name: the front-end dispatches the optional scan step
# via getattr(backend, "scan").
scan = o4_scan


# --- Generic-contract additions (Task 3) -------------------------------------
# The front-end dispatches the scan step via backend.scan_requested(ns) and
# prints the dry-run plan via backend.scan_plan(ns). These additions keep
# the pre-pluto behavior byte-identical (o4-scan is the only hackrf scan).

SCAN_SCENARIOS = frozenset({"o4-scan"})


def scan_requested(ns) -> bool:
    """True when ``ns.scenario`` resolves its center via a scan step."""
    return ns.scenario in SCAN_SCENARIOS


def scan_plan(ns) -> None:
    """Dry-run plan output for the scan scenario."""
    bins = o4_scan_bins()
    print(f"o4-scan plan: {len(bins)} bins, dwell {ns.dwell_s} s")
    print("bins (MHz): " + " ".join(str(b) for b in bins))
