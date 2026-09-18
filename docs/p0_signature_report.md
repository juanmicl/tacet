# P0 RF signature report

Status: pipeline, CLI and self-tests in place (`uv run python -m tests`).
**Results pending the bench session**; every number below is a placeholder
to be filled from `notebooks/01_bench_overview.ipynb`.

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
- Every session includes ONE TX-OFF control capture (`protocol: noise`).
  Signal features are reported as measured, and interpreted only against
  that control: whatever shows up in the control (noise, spurs, other
  users of the band) is not the target's signature.
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

| metric | value |
| --- | --- |
| occupied bandwidth (99%) | ___ MHz |
| duty cycle (-35 dB rel.) | ___ |
| spectral flatness | ___ |
| mean burst duration | ___ s |
| mean gap duration | ___ s |
| rail fraction (whole capture) | ___ |
| vs TX-OFF control (mean PSD ratio) | ___ dB |

- Per-window results: 2420 MHz: ___ , 2450 MHz: ___ , 2465 MHz: ___.
- TX power as configured on the Boxer: ___ mW (recorded per capture).
- Session: ___ (id), date: ___.

**Headline: Measured on: ___ (session id).**

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
