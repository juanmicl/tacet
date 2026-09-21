# P0 RF signature report

Status: pipeline, CLI and self-tests in place (`uv run python -m tests`).
**ELRS results measured** (2026-09-19 bench session, notebook
`notebooks/01_bench_overview.ipynb` executed with outputs). DJI O4
pending hardware session.

## Goal

Characterize the RF signatures of our actual target links at the bench —
the ELRS 2.4 GHz control link and the DJI O4 5.1/5.8 GHz video link — with
measurable, publishable numbers: occupied bandwidth, duty cycle, spectral
flatness, burst structure and clipping QC. Detection claims later in the
project are only as good as these measured references.

## Method

- HackRF One, cs8 (complex int8) at 20 Msps, receive-only
  (`hackrf_transfer -r` via the `tacet capture` CLI). Capture protocol,
  gains and safety rules: `docs/capture_protocol_p0.md`.
- Every capture is indexed in `manifest.json` (device, protocol, purpose,
  center frequency, gains, antenna, operator notes, sha256 per channel).
- Per capture, `tacet.dsp.features` computes:
  1. QC over the whole file: `rail_fraction`, streamed block by block
     (int8 rails = clipping; above 0.1% the capture is invalid).
  2. Features on the first 2 s segment: DC removal, Welch PSD, 99%
     occupied bandwidth, spectral flatness (geometric/arithmetic mean
     ratio), time envelope with the center-DC bins excluded, duty cycle
     and burst structure at -35 dB relative to the envelope peak.
- Every session includes ONE TX-OFF control capture per window at the
  same gain (`protocol: noise`). Signal features are reported as measured
  and interpreted only against the matched control: whatever shows up in
  the control (noise, spurs, other users of the band) is not the target's
  signature. SNR is reported two ways: window-mean PSD ratio (labeled
  LOWER BOUND, since averaging over 20 MHz dilutes FHSS energy) and
  per-frequency-bin ratio (median over bins where signal exceeds the
  control by >3 dB). The duty-cycle threshold is derived from the
  matched control's envelope (95th percentile), not from a constant.
- All primitives have synthetic self-tests; the numbers below come from
  recordings, not simulations.

## Results — DJI O4 video link (5.1/5.8 GHz)

| metric | value |
| --- | --- |
| occupied bandwidth (99%) | ___ MHz |
| duty cycle (-35 dB rel.) | ___ |
| spectral flatness | ___ |
| mean burst duration | ___ s |
| mean gap duration | ___ s |
| rail fraction (whole capture) | ___ |
| vs TX-OFF control (mean PSD ratio) | ___ dB |

- Center frequency used: ___ MHz (from the `o4-scan` winner).
- Session: ___ (id), date: ___.

## Results — ELRS control link (2.4 GHz ISM)

Measured 2026-09-19, bench, Boxer only (drone off), RadioMaster Boxer at
100 mW fixed TX power, ~1 m from HackRF antenna, indoor.

| metric | 2440 MHz @ 16 dB | 2450 MHz @ 16 dB | 2465 MHz @ 16 dB | 2450 re-scan |
| --- | --- | --- | --- | --- |
| occupied BW (99%) | 19.5 MHz | 19.5 MHz | 19.5 MHz | 19.5 MHz |
| block duty (mean ± σ, 100 ms) | 35.3% ± 18.3% | 7.2% ± 10.6% | 31.6% ± 14.5% | 29.1% ± 12.5% |
| rail fraction (whole) | 0.000000% | 0.000000% | 0.000000% | 0.000000% |
| SNR lower bound (window-mean) | +11.8 dB | -0.0 dB | +13.0 dB | +7.4 dB |
| per-bin occupied fraction | 88.3% | 4.1% | 94.0% | 81.9% |
| median per-bin SNR (occupied) | +13.1 dB | +4.1 dB | +12.9 dB | +7.3 dB |
| matched control (id) | 005401Z | 005404Z | 005408Z | 005404Z |

Notes:
- Gain setting: lna 8 dB / vga 8 dB (clean at 100 mW, ~1 m; 32/32
  saturates — rail QC rejects those captures).
- Occupied bandwidth fills the full 20 MHz window in active captures:
  expected for a frequency-hopping link visiting the whole ISM band.
- The 2450 MHz window was captured twice: the first time showed
  ambient-level duty (7.2%), the re-scan minutes later showed 29.1%.
  The block-by-block analysis (30 × 100 ms blocks) shows the separation
  (21.9 pp) is below the block-to-block spread (23.1 pp): the temporal
  lumpiness of the FHSS sequence is **not yet established** at this
  capture length. A 60 s capture would settle it.
- A 16/16 capture at 2440 MHz (not shown in the table; higher gain)
  showed +21.0 dB window-mean SNR with 93.8% occupied bins, confirming
  the signal is ELRS, not noise.
- TX power as configured on the Boxer: 100 mW (20 dBm).
- Measurements taken with transmitter only (no receiver connected, no
  telemetry). Duty cycle may shift slightly when the drone is linked
  (telemetry slots and link adaptation change the packet pattern).
- Session: 20260919T004638Z through 20260919T005913Z (5 signal + 5
  matched control recordings, ~10 min total bench time).

**Headline: ELRS at 100 mW, ~1 m, lna 8/vga 8: consistently detectable
(+7 to +13 dB window-mean SNR) when the FHSS sequence is visiting the
observed window. Single-window duty cycle is NOT a stable metric at 3 s
captures — detector design must account for dwell time per window.**

### Detector-design implications

1. **Dwell time matters**: 3 s can see near-zero duty (7.2%) or heavy
   activity (31.6%) at the same frequency depending on when the FHSS
   sequence passes through. A detector that integrates longer or watches
   multiple windows simultaneously will be more robust.
2. **Per-bin SNR is the right metric for FHSS**: the window-mean dilutes
   the signal by averaging over empty hops. The occupied-bin fraction
   (82-94% when active) and the median per-bin ratio (+7 to +13 dB) are
   the honest detection margins.
3. **No clipping at bench distance with 8/8 gains**: the signal chain
   has headroom. At longer ranges, gains can go up before hitting rails
   (the saturated 32/32 capture shows 3.9% rails at ambient WiFi alone).

## Honest-results note

If the numbers come out messier than the textbook pictures — chirps too
sparse inside one 20 MHz window, video duty cycle drifting with scene
content, flatness polluted by background carriers — the measured values
stand as they are. They are the reference the detector will be held to;
the honest number is still the deliverable, and a detection threshold that
only works on clean synthetic data will be documented as exactly that.

## Interpretation guide

Full plots and per-capture detail: `notebooks/01_bench_overview.ipynb`.
Reading rules of thumb:

- Flatness near 1 with a wide occupied bandwidth and a bursty envelope is
  the OFDM-like video signature; flatness near 0 means a few dominant
  bins (tones), which is neither O4 nor noise.
- ELRS chirps appear as diagonal ridges in the spectrogram; a single
  20 MHz window sees only the hops landing inside it, so the reported duty
  cycle is a per-window figure, not the whole link.
- Anything present at equal level in the TX-OFF control is background, not
  signature.
- Rail fraction above 0.1% invalidates the capture (clipping): drop gains
  and re-record per the capture protocol.

## Safety

Receive-only throughout: the HackRF runs `hackrf_transfer -r` and nothing
else. No jamming, no injection, no transmissions from our equipment. Bench
transmissions come only from the Boxer and the quads under test, props
off, at legal powers.

## Reproducing

Run `notebooks/01_bench_overview.ipynb` against the project venv. Without
captures it executes top-to-bottom on clearly labeled synthetic fallback
data (pipeline demonstration only; those numbers are not measurements).
Capture sessions follow `docs/capture_protocol_p0.md`.
