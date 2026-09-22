# P0 RF signature report

Status: pipeline, CLI and self-tests in place (`uv run python -m tests`).
**ELRS results measured** (2026-09-19 bench session, notebook
`notebooks/01_bench_overview.ipynb` executed with outputs). DJI O4
**blocked by receiver sensitivity**: HackRF One noise figure at 5.8 GHz
is too high; Pluto+ (AD9363) required (see Results section below).

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

Status: **characterization blocked by receiver sensitivity**. The O4 air
unit transmits (goggles receive video at all times), but the HackRF One
cannot resolve the signal at 5.x GHz. Measured 2026-09-22.

### What we tested

| Test | Antenna | Gain | Result |
| --- | --- | --- | --- |
| Dual-band 2.4/5.8, 5.1 GHz, ~1 m | dual-band whip | 8/8, 16/16, 32/32 | no signal above noise |
| Dual-band, 5.8 GHz, ~1 m | dual-band whip | 16/16 | no signal above noise |
| Dual-band, 5.8 GHz, 0 cm (antennas touching) | dual-band whip | 8/8 | no signal above noise |
| Patch 9.4 dBi LHCP, 5.8 GHz, ~1 m | Aihasd Triple Feed Patch | 16/16 | no signal above noise |
| Patch 9.4 dBi LHCP, 5.8 GHz, ~30 cm | Aihasd Triple Feed Patch | 32/32 | marginal (~2 dB above matched control) |
| WiFi 5 GHz (reference, routers) | Aihasd Triple Feed Patch | 32/32 | CLEAR: 5180 MHz +26.7 dB, multiple channels visible |

The WiFi reference proves the antenna, coax, and HackRF work correctly at
5.x GHz for sufficiently strong signals (WiFi routers emit 100+ mW). The
O4 air unit (10-50 mW, unconfirmed) falls below the HackRF One's internal
noise at 5.8 GHz.

### Root cause: HackRF One noise figure at 5.8 GHz

- HackRF dev mailing list describes the device as "extremely deaf" at
  higher frequencies without an external LNA.
- HackRF Pro (announced Dec 2025) improves the noise figure significantly
  but still recommends an LNA for weak-signal reception.
- Projects that successfully detect DJI signals at 5.8 GHz use more
  sensitive SDRs: DroneSecurity uses a USRP B210 (~2,000 EUR); the ANTSDR
  project uses dedicated hardware (~200 EUR). None report success with
  the HackRF One.

### Hardware decision

The Pluto+ (AD9363, ~180 EUR) is the correct platform for O4
characterization:
- noise figure ~3-5 dB (vs HackRF's 8-15+ at 5.8 GHz)
- 12-bit ADC (24 dB more dynamic range than HackRF's 8-bit)
- already planned for P3 passive radar (dual coherent RX)
- the coherence characterization experiment (notebook 02) is already
  built and waiting for the hardware

Until the Pluto+ arrives, O4 characterization remains blocked. The HackRF
One is adequate for 2.4 GHz ELRS detection (demonstrated at +13 dB SNR
at 1 m) and general wideband spectrum scanning.

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

1. **One 20 MHz window is sufficient**: the FHSS visits all frequencies
   uniformly. A 10-pass sweep across the full ISM band (2400-2480 MHz,
   5 positions, ~50 s) showed every window active in every pass with
   consistent duty (18-35% depending on band edge). No temporal
   clustering, no frequency gaps. Detection range is a link-budget
   question, not a coverage question.
2. **Link budget calibrated**: at 5 cm the signal arrives at +17 dB
   peak with lna 16/vga 16. At ~1 m it measured -18 dB (yesterday,
   consistent with FSPL). At 100-500 m the expected signal level
   (-60 to -74 dBm) leaves 15-30 dB of margin above the HackRF noise
   floor, supporting the 0.3-2 km detection goal for the RF layer.
3. **Per-bin SNR is the right metric for FHSS**: the window-mean dilutes
   the signal by averaging over empty hops. The occupied-bin fraction
   (82-94% when active) and the median per-bin ratio (+7 to +13 dB) are
   the honest detection margins.
4. **No clipping at bench distance with 8/8 gains**: the signal chain
   has headroom. At longer ranges, gains can go up before hitting rails
   (the saturated 32/32 capture shows 3.9% rails at ambient WiFi alone).
5. **Signal level varies with exact antenna distance**: captures at
   nominally the same bench distance showed ~9 dB variation, consistent
   with 2-3x actual distance differences (6 dB per doubling). Calibrated
   distance measurements (walk-tests) will resolve this.

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
