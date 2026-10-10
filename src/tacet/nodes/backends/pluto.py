"""Pluto+ (AD9363) capture backend — RECEIVE ONLY, dual-RX capable.

Device driver for the Pluto+ bench scenarios; implements the same
contract surface as ``backends/hackrf.py`` for the front-end
(tacet.nodes.capture): ``SCENARIOS``, ``validate(args)``,
``check_available()``, ``capture_once(ns, session_dir, index) -> dict |
None``, ``entry_parts(ns, path, sha256, warn="")``, the optional
``scan`` (``fm_scan``) plus its ``scan_requested``/``scan_plan``
predicates, and ``print_plan`` for the dry-run preview.

Receive-only discipline (hard project constraint):
- ``open_pluto`` mutes BOTH TX channels at -89.75 dB BEFORE any other
  write, never touches the TX buffers and never calls ``tx()``: the
  Pluto+ is used strictly as a coherent receiver.
- Pinned pyadi write order: TX mutes -> ``sample_rate`` ->
  ``rx_rf_bandwidth`` -> ``rx_lo`` -> ``gain_control_mode_chan{0,1}`` ->
  ``rx_hardwaregain_chan{0,1}`` -> ``rx_enabled_channels`` ->
  ``rx_buffer_size`` -> ``_rxadc.set_kernel_buffers_count(1)``.

Rate guard: the first ``rx()`` buffer is read and discarded (pipeline
warm-up); each subsequent read is timed via the ``pluto._clock`` seam
(default ``time.monotonic``); the first measured window is dropped as
jitter and the achieved rate is estimated over the remaining >= 3
windows. Below 90% of the requested rate, mono scenarios ABORT with a
ValueError (zero-orphan: nothing has been written yet; the front-end
turns it into ``error: ...`` + exit code 2) while dual scenarios WARN
and record the achieved rate in the sidecar and, via ``notes_warn``,
in the entry's operator_notes.

Sidecars: the Pluto+ backend writes one JSON sidecar per capture
(``<base>.json`` with the rate-guard evidence and sha256s); hackrf's
sidecars are plain-text ``.log`` files — different formats by design.

Zero-orphan: on any failure (device error, KeyboardInterrupt, channel
write error) the capture's FULL file set — channel files plus sidecar —
is deleted; the manifest append itself is front-end-owned and its
failure path removes the same file set via the ``sidecar_path`` extra.

Test seams: ``pluto._set_device_factory(callable)`` replaces the pyadi
constructor and ``pluto._clock`` drives the rate guard; the test suite
resets both in try/finally.
"""

import json
import math
import sys
import time
from pathlib import Path

import numpy as np

from tacet.loaders import manifest as manifest_mod
from tacet.loaders.migration import band_for_freq_hz

BYTES_PER_SAMPLE = 4          # cs16: 2 bytes I + 2 bytes Q per sample

RATE_GUARD_MIN_RATIO = 0.90   # below 90% of the requested rate -> guard
MIN_RATE_WINDOWS = 3          # rate estimated over >= 3 timed windows
MONO_BUFFER_SAMPLES = 2 ** 20       # ~52 ms per rx() at 20 Msps

TX_MUTE_DB = -89.75           # both TX channels muted FIRST (receive-only)

SCAN_SETTLE_S = 0.1           # discarded after each retune (PLL settling)
SCAN_SPAN_MHZ = (88.0, 108.0) # FM broadcast band
SCAN_STEP_MHZ = 0.2           # 200 kHz bins
SCAN_BUFFER_S = 0.3           # per-bin buffer (settle + averaging window)

FM_DEFAULT_MHZ = 98.5         # coherence-cal default (FM broadcast carrier)
FILE_EXT = ".cs16"

SCENARIOS = {
    "coherence-cal": "dual-RX coherence calibration on an FM broadcast "
                     "carrier (--scan-fm finds the strongest 88-108 MHz "
                     "bin first)",
    "o4-capture": "record one fixed DJI O4 center frequency (single RX)",
    "control": "TX-OFF control capture (protocol noise) for the session "
               "(single RX)",
}

# Scenario-defined configuration: sample rate, RX channel count, protocol,
# purpose, duration envelope, and the rf_chain note that goes into
# operator_notes. Duration caps are RAM-bound, not disk-bound: pyadi rx()
# delivers complex128 (16 B/sample) and capture_once accumulates ALL
# buffers before one concatenate (~2x peak), so dual 2.56 Msps costs
# ~40 MB of RAM per recorded second and 20 Msps mono ~320 MB/s. The
# planned follow-up that removes these caps is per-buffer int16
# conversion with incremental channel-file writes inside the rx loop
# (keeping the full-file-set zero-orphan semantics).
SCENARIO_CONFIG = {
    "coherence-cal": {
        "rate": 2_560_000, "dual": True, "protocol": "fm_carrier",
        "purpose": "coherence_cal", "rf_chain": "rx1+rx2 coherent dual",
        "max_duration_s": 120.0,
    },
    "o4-capture": {
        "rate": 20_000_000, "dual": False, "protocol": "dji_o4",
        "purpose": "signature_capture", "rf_chain": "rx1",
        "max_duration_s": 15.0,
    },
    "control": {
        "rate": 2_560_000, "dual": False, "protocol": "noise",
        "purpose": "signature_capture", "rf_chain": "rx1",
        "max_duration_s": 120.0,
    },
}

# coherence-cal sessions live under data/coherence_cal/<UTC session>/
# (notebook 02's layout); everything else stays in signature_capture.
SESSION_SUBDIR = {"coherence-cal": "coherence_cal"}

# Scan dispatch (generic contract, like hackrf's o4-scan): coherence-cal
# scans only when --scan-fm was given.
SCAN_SCENARIOS = frozenset({"coherence-cal"})

# Test seam: replaces the pyadi constructor when set (None = real device).
_device_factory = None
# Test seam: monotonic clock driving the rate guard.
_clock = time.monotonic


def _set_device_factory(factory) -> None:
    """Install a device factory for tests (pass None to restore pyadi)."""
    global _device_factory
    _device_factory = factory


def _make_device(uri: str):
    if _device_factory is not None:
        return _device_factory(uri)
    import adi  # lazy: pyadi-iio is the `acquisition` extra
    return adi.ad9361(uri=uri)


def bytes_per_second(scenario: str) -> int:
    """On-disk byte rate for the front-end's disk-headroom estimate.

    cs16 at the scenario's sample rate, one file per RX channel (dual
    coherence: ~20.5 MB/s; mono 20 Msps: ~80 MB/s) — matches what the
    capture actually writes, not the worst case across scenarios.
    """
    cfg = SCENARIO_CONFIG[scenario]
    return BYTES_PER_SAMPLE * cfg["rate"] * (2 if cfg["dual"] else 1)


def validate(args) -> None:
    """Validate parsed CLI args against the Pluto+ envelopes.

    Envelope: 70-6000 MHz, gain 0-74.5 dB (continuous, no step sizes),
    duration 1 s up to the scenario's RAM-bound cap (15 s for the
    20 Msps mono o4-capture, 120 s for the 2.56 Msps scenarios; see
    SCENARIO_CONFIG for the arithmetic). Raises ValueError with a
    user-facing message; the front-end turns it into exit code 2
    before anything is touched.
    """
    if getattr(args, "scan_fm", False) and args.freq_mhz is not None:
        raise ValueError(
            "--scan-fm resolves the center frequency; drop --freq-mhz")
    if args.freq_mhz is not None and not 70 <= args.freq_mhz <= 6000:
        raise ValueError(
            f"--freq-mhz must be within 70-6000 MHz, got {args.freq_mhz}")
    limit = SCENARIO_CONFIG[args.scenario]["max_duration_s"]
    if not 1.0 <= args.duration_s <= limit:
        raise ValueError(f"--duration-s must be 1-{limit:g} s for "
                         f"{args.scenario}, got {args.duration_s}")
    if (args.lna, args.vga) != (32, 32):
        raise ValueError(
            "--lna/--vga are HackRF flags; set the Pluto+ gain with "
            "--gain-db")
    if args.gain_db is None:
        raise ValueError(
            "--gain-db is required for pluto (RX hardware gain, 0-74.5 dB)")
    if not 0 <= args.gain_db <= 74.5:
        raise ValueError(
            f"--gain-db must be 0-74.5 dB (continuous), got {args.gain_db}")


def check_available() -> None:
    """Raise ValueError when pyadi-iio (the acquisition extra) is missing.

    ValueError (not RuntimeError like hackrf's PATH check) because the
    failure is an environment/dependency problem, not a device one; the
    front-end catches both and prints the message verbatim, exit code 2.
    """
    try:
        import adi  # noqa: F401
    except ImportError as exc:
        raise ValueError(
            "pyadi-iio is required for the Pluto+ backend. "
            "Install it with: uv sync --extra acquisition") from exc


def build_center_hz(scenario: str, ns) -> float:
    """Center frequency (MHz) for a scenario from parsed args.

    coherence-cal defaults to the 98.5 MHz FM broadcast carrier;
    o4-capture and control require --freq-mhz.
    """
    if ns.freq_mhz is not None:
        return float(ns.freq_mhz)
    if scenario == "coherence-cal":
        return FM_DEFAULT_MHZ
    raise ValueError(f"--freq-mhz is required for {scenario}")


def open_pluto(uri: str, center_hz: float, gain_db: float, sample_rate: int,
               dual: bool):
    """Open and configure the AD9361 for one capture (pinned order).

    RECEIVE ONLY: both TX channels are muted at -89.75 dB FIRST, the TX
    buffers are never touched and tx() is never called.
    """
    sdr = _make_device(uri)
    channels = [0, 1] if dual else [0]
    # ~1 s per rx() in dual coherence mode; the widest sane mono chunk
    # (~52 ms at 20 Msps) for signature captures.
    buffer_size = int(sample_rate) if dual else MONO_BUFFER_SAMPLES
    # --- pinned pyadi write order (do not reorder) ----------------------
    sdr.tx_hardwaregain_chan0 = TX_MUTE_DB      # RECEIVE ONLY: mute TX first
    sdr.tx_hardwaregain_chan1 = TX_MUTE_DB
    sdr.sample_rate = int(sample_rate)
    sdr.rx_rf_bandwidth = int(sample_rate)
    sdr.rx_lo = int(center_hz)
    sdr.gain_control_mode_chan0 = "manual"
    sdr.gain_control_mode_chan1 = "manual"
    sdr.rx_hardwaregain_chan0 = float(gain_db)
    sdr.rx_hardwaregain_chan1 = float(gain_db)
    sdr.rx_enabled_channels = channels
    sdr.rx_buffer_size = buffer_size
    sdr._rxadc.set_kernel_buffers_count(1)
    return sdr


def _channel_paths(base: Path, dual: bool) -> list:
    """Channel file set for a capture base path (ch1/ch2 when dual)."""
    if not dual:
        return [base]
    return [base.with_name(f"{base.stem}_ch1{base.suffix}"),
            base.with_name(f"{base.stem}_ch2{base.suffix}")]


def entry_parts(ns, path: str, sha256, warn: str = "") -> dict:
    """Pluto+ manifest-entry parts (the backend-owned fragments).

    ``path`` is the capture base path; dual scenarios expand it to the
    ``_ch1``/``_ch2`` pair. ``sha256`` is one hash (mono, or a
    placeholder for every channel in dry-runs) or a per-channel list.
    ``warn`` is the warning suffix (rate/byte-count) for operator_notes.
    """
    cfg = SCENARIO_CONFIG[ns.scenario]
    center_hz = float(ns.freq_mhz) * 1e6
    paths = [str(p) for p in _channel_paths(Path(path), cfg["dual"])]
    shas = (list(sha256) if isinstance(sha256, (list, tuple))
            else [sha256] * len(paths))
    channels = [{"path": p, "sha256": s} for p, s in zip(paths, shas)]
    gain = {"mode": "manual", "rx1_db": float(ns.gain_db)}
    if cfg["dual"]:
        gain["rx2_db"] = float(ns.gain_db)
    return {
        "device": "pluto_plus",
        "protocol": cfg["protocol"],
        "purpose": cfg["purpose"],
        "center_freq_hz": center_hz,
        "sample_rate_sps": float(cfg["rate"]),
        "format": "cs16",
        "band": band_for_freq_hz(center_hz),
        "channels": channels,
        "gain": gain,
        "notes_warn": warn,
    }


def _write_cs16(path: Path, samples) -> None:
    """Write one channel as headerless interleaved int16 I/Q (raw counts).

    Matches tacet.loaders.cs16 exactly: I first, native int16, no
    normalization (conversion to complex64 happens only at load time).
    """
    z = np.asarray(samples)
    iq = np.empty(z.size * 2, dtype=np.int16)
    iq[0::2] = np.clip(np.real(z), -32768, 32767)
    iq[1::2] = np.clip(np.imag(z), -32768, 32767)
    path.parent.mkdir(parents=True, exist_ok=True)
    iq.tofile(str(path))


def _discard(paths, sidecar: Path) -> None:
    """Zero-orphan cleanup: remove the capture's FULL file set."""
    for p in paths:
        Path(p).unlink(missing_ok=True)
    Path(sidecar).unlink(missing_ok=True)


def _write_sidecar(path: Path, ns, cfg: dict, achieved: float, ratio: float,
                   n_reads: int, samples: int, shas: list) -> None:
    """Sidecar JSON with the rate-guard evidence for one capture."""
    doc = {
        "device": "pluto_plus",
        "scenario": ns.scenario,
        "uri": ns.uri,
        "requested_rate_sps": float(cfg["rate"]),
        "achieved_rate_sps": float(achieved),
        "rate_ratio": float(ratio),
        "rate_guard_min_ratio": RATE_GUARD_MIN_RATIO,
        "buffers_discarded": 1,
        "buffers_measured": int(n_reads),
        "rate_windows": int(min(MIN_RATE_WINDOWS, n_reads - 1)),
        "samples_per_channel": int(samples),
        "dual": bool(cfg["dual"]),
        "gain_db": float(ns.gain_db),
        "sha256": list(shas),
    }
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def capture_once(ns, session_dir: Path, index: int) -> dict | None:
    """Run one Pluto+ capture; return manifest entry parts.

    Backend-owned: device open + pinned config, the buffered rx() loop
    with the rate guard, per-channel cs16 writes, byte-count check,
    sha256 per file, and the sidecar JSON. Returns None when the device
    run failed (full file set removed). KeyboardInterrupt removes the
    partial capture and re-raises. The mono rate-guard abort raises
    ValueError BEFORE anything is written (zero-orphan). It does NOT
    append to the manifest and does NOT prune session dirs — the
    front-end owns those.
    """
    cfg = SCENARIO_CONFIG[ns.scenario]
    rate, dual = cfg["rate"], cfg["dual"]
    buffer_size = int(rate) if dual else MONO_BUFFER_SAMPLES
    center_hz = float(ns.freq_mhz) * 1e6
    target = int(round(ns.duration_s * rate))
    # The warm-up buffer is discarded; the first measured window is jitter
    # and the rate is judged over the remaining >= MIN_RATE_WINDOWS, so at
    # least MIN_RATE_WINDOWS + 1 buffers are read after the discard.
    n_reads = max(MIN_RATE_WINDOWS + 1, math.ceil(target / buffer_size))
    base = Path(session_dir) / f"{ns.scenario}_{index:03d}{FILE_EXT}"
    paths = _channel_paths(base, dual)
    sidecar = base.with_suffix(".json")

    try:
        sdr = open_pluto(ns.uri, center_hz, ns.gain_db, rate, dual)
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        print(f"pluto: device open failed: {exc}", file=sys.stderr)
        return None

    try:
        sdr.rx()                       # warm-up: read and discarded
        bufs, windows = [], []
        for _ in range(n_reads):
            t0 = _clock()
            buf = sdr.rx()
            windows.append(_clock() - t0)
            bufs.append(buf)
    except KeyboardInterrupt:
        _discard(paths, sidecar)
        print("interrupted; partial capture removed", file=sys.stderr)
        raise
    except Exception as exc:
        _discard(paths, sidecar)
        print(f"pluto: rx failed: {exc}", file=sys.stderr)
        return None

    # Rate guard: drop the first measured window (jitter); judge on >= 3.
    # Lengths come from the raw buffers (per channel) so a hostile channel
    # fails later, at its write, after the healthy channel hit the disk.
    rate_samples = sum(len(b[0] if dual else b) for b in bufs[1:])
    rate_elapsed = sum(windows[1:])
    achieved = (rate_samples / rate_elapsed if rate_elapsed > 0
                else math.inf)
    ratio = achieved / rate
    warn = ""
    if ratio < RATE_GUARD_MIN_RATIO:
        detail = (f"achieved {achieved/1e6:.3f} of {rate/1e6:.3f} Msps "
                  f"({ratio*100:.1f}%)")
        if dual:
            warn = f" [rate warning: {detail}]"
            print(f"WARNING:{warn}", file=sys.stderr)
        else:
            _discard(paths, sidecar)   # nothing written yet; belt & braces
            raise ValueError(
                f"pluto rate guard ({ns.scenario}): {detail}; mono capture "
                "aborted (zero-orphan)")

    per_channel = []
    if dual:
        groups = [[np.asarray(buf[i]) for buf in bufs] for i in (0, 1)]
    else:
        groups = [[np.asarray(buf) for buf in bufs]]
    for chunks in groups:
        z = np.concatenate(chunks)
        per_channel.append(z[:target] if z.size >= target else z)

    try:
        for path, z in zip(paths, per_channel):
            _write_cs16(path, z)
    except KeyboardInterrupt:
        _discard(paths, sidecar)
        print("interrupted; partial capture removed", file=sys.stderr)
        raise
    except Exception as exc:
        _discard(paths, sidecar)
        print(f"pluto: channel write failed: {exc}", file=sys.stderr)
        return None

    # Post-write assembly (byte-count check, sha256, sidecar, entry
    # parts) runs under the same zero-orphan umbrella: any failure here
    # leaves the channel files orphaned, so the cleanup owns the FULL
    # file set inside the backend, where it is known. main keeps its
    # existing handlers (ValueError -> house error + rc 2).
    try:
        expected = BYTES_PER_SAMPLE * target * len(paths)
        actual = sum(p.stat().st_size for p in paths)
        if actual != expected:
            mismatch = (f" [byte-count warning: expected {expected}, got "
                        f"{actual} — possible dropped samples]")
            warn = warn + mismatch
            print(f"WARNING:{mismatch}", file=sys.stderr)

        shas = [manifest_mod.compute_sha256(str(p)) for p in paths]
        _write_sidecar(sidecar, ns, cfg, achieved, ratio, n_reads,
                       per_channel[0].size, shas)
        # rf_chain context belongs to the entry's operator_notes (the
        # schema's rf_chain object stays reserved for splitter/cable
        # metadata).
        ns.notes = (ns.notes + f" [rf_chain: {cfg['rf_chain']}]").strip()
        parts = entry_parts(ns, str(base), shas if dual else shas[0],
                            warn=warn)
        parts["size_bytes"] = actual
        parts["sidecar_path"] = str(sidecar)
        return parts
    except BaseException:
        _discard(paths, sidecar)
        raise


def fm_scan_bins() -> list:
    """FM broadcast scan bins: 88-108 MHz in 200 kHz steps.

    Exclusive at 108 MHz: band_for_freq_hz maps [87.5, 108) so a
    108.0 MHz winner would have no band (and would fail inside
    entry_parts, after the files were written).
    """
    lo, hi = SCAN_SPAN_MHZ
    count = int(round((hi - lo) / SCAN_STEP_MHZ))
    # Exclusive at hi: band_for_freq_hz maps [87.5, 108), so a 108.0 MHz
    # winner would have no band (and would fail inside entry_parts, after
    # the files were written). tests pin bins[-1] < hi.
    return [round(lo + i * SCAN_STEP_MHZ, 6) for i in range(count)]


def fm_scan(ns, session_dir: Path):
    """Scan 88-108 MHz for the strongest FM carrier; return winning MHz.

    Per bin: retune ``rx_lo``, one buffer, discard the first 0.1 s
    (PLL settling, mirroring hackrf's SCAN_SETTLE_S), then average the
    power over the rest. No dwell files are written. Appends winner and
    power to ns.notes; returns None when no bin produced a reading.
    """
    cfg = SCENARIO_CONFIG[ns.scenario]
    rate = cfg["rate"]
    bins = fm_scan_bins()
    settle = int(SCAN_SETTLE_S * rate)
    span = int(SCAN_BUFFER_S * rate)
    try:
        sdr = open_pluto(ns.uri, bins[0] * 1e6, ns.gain_db, rate,
                         cfg["dual"])
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        print(f"pluto: scan open failed: {exc}", file=sys.stderr)
        return None
    best_mhz, best_power = None, -math.inf
    for mhz in bins:
        try:
            sdr.rx_lo = int(mhz * 1e6)
            z = sdr.rx()
        except KeyboardInterrupt:
            raise
        except Exception:
            continue                     # dead bin: keep scanning
        z0 = np.asarray(z[0] if isinstance(z, (list, tuple)) else z)
        seg = z0[:span][settle:]         # settle discard after the retune
        if seg.size == 0:
            continue
        power = float(np.mean(np.abs(seg) ** 2))
        if power > best_power:
            best_mhz, best_power = mhz, power
    if best_mhz is not None:
        note = f"[fm-scan winner {best_mhz} MHz, power {best_power:.4g}]"
        ns.notes = (ns.notes + " " + note).strip()
    return best_mhz


# Generic contract name: the front-end dispatches the optional scan step
# via getattr(backend, "scan").
scan = fm_scan


def scan_requested(ns) -> bool:
    """True when the scenario resolves its center via a scan step."""
    return ns.scenario in SCAN_SCENARIOS and bool(getattr(ns, "scan_fm",
                                                          False))


def scan_plan(ns) -> None:
    """Dry-run plan output for the scan scenario."""
    bins = fm_scan_bins()
    print(f"fm-scan plan: {len(bins)} bins, 88-108 MHz step 200 kHz, "
          f"settle {SCAN_SETTLE_S} s per bin")
    print("bins (MHz): " + " ".join(str(b) for b in bins))


def print_plan(ns, rel_path: str) -> None:
    """Dry-run config preview (front-end prints the entry JSON itself)."""
    cfg = SCENARIO_CONFIG[ns.scenario]
    channels = ", ".join(str(p) for p in
                         _channel_paths(Path(rel_path), cfg["dual"]))
    print(f"pluto plan (dry-run): uri={ns.uri} "
          f"center={float(ns.freq_mhz):g} MHz rate={cfg['rate']/1e6:g} Msps "
          f"gain={float(ns.gain_db):g} dB dual={cfg['dual']} "
          f"duration={float(ns.duration_s):g} s")
    print(f"channels: {channels}")
    print("config: tx muted at -89.75 dB both channels (receive-only)")
