# Tacet — Project Context (AGENTS.md)

## What this project is

Tacet is a **passive (receive-only) drone detection system**: low-cost sensing
nodes that detect and track drones around critical infrastructure (prisons,
solar farms, ports, data centers) where airport-grade counter-drone systems
are unaffordable. Three sensing layers fused by a Bayesian multi-target
tracker:

1. **RF signatures** — ELRS control links (2.4 GHz, LoRa/CSS chirps,
   frequency-hopping), DJI O3/O4 video links (OFDM, 5.1–5.8 GHz), analog FPV
   carriers (5.8 GHz FM, ~8 MHz wide). Range goal: 0.3–2 km.
2. **Acoustic** (later phase) — 4–8 MEMS I2S mics on ESP32, SRP-PHAT
   beamforming for bearing, rotor harmonics (blade-pass frequency =
   blades × RPM/60) for classification. Range: 100–300 m for a 5" quad.
3. **Passive radar** (later phase) — Pluto+ (AD9363, dual coherent RX) —
   DVB-T towers as illuminators of opportunity. Clutter cancellation
   (adaptive filtering), cross-ambiguity function, CFAR detection,
   propeller micro-Doppler. Pluto+ chosen over KrakenSDR: single-chip
   coherence + 20 MHz BW (DVB-T needs 8 MHz; RTL-SDR taps can't). TX on
   the Pluto+ MUST stay disabled — receive-only project.

Fusion/tracking: **Stone Soup** (GNN/JPDA data association, EKF/UKF). Each
sensor produces measurements with covariance; the tracker fuses them.

**Hard constraints:**

- Receive-only. NO jamming, NO interception of communication content, NO
  neutralization features. This is a legal requirement for private operators
  in the EU, not a preference.
- Everything must be measurable: detectors are validated with ROC curves at
  multiple distances, not with magic confidence thresholds.
- Hardware budget mindset: HackRF (~€350), RTL-SDR (~€30/node), ESP32 (~€8),
  Pluto+ (~€180). No prime-contractor assumptions.

## Owner

Solo founder, data engineer / data scientist with quant research background.
Writes Python (numpy/scipy, TimescaleDB/Postgres, Numba, XGBoost), some
Arduino/ESP32. Communicate in **Spanish**, code/comments/docs in **English**.

## Hardware available right now

- HackRF One (SMA female ports) + dual-band 2.4/5.8 GHz antenna
  (SMA male) + FPV goggle antennas (5.8 GHz, SMA male)
- RadioMaster Boxer (ELRS 2.4 GHz, configurable TX power 10–250 mW)
  — usable as a calibrated test transmitter WITHOUT flying
- Test targets: 5" FPV quad (ELRS control + DJI O4 digital video) and a
  250 g FPV quad. The 5" is >250 g but is a custom Betaflight build →
  does NOT broadcast standard Remote ID (EU homebuilt exemption). It DOES
  embed DJI proprietary DroneID in the O4 link (decoding = research track,
  not a near-term deliverable).
- 2× Raspberry Pi 3B+ (future node brains — NOT capable of continuous
  20 Msps DSP), several ESP32s, SIM800C modules (future SMS alert channel
  only — 2G is dying in Spain, do not build anything critical on it)

Planned (decided, not yet purchased): Pluto+ SDR (AD9363, 2× RX coherent,
70 MHz–6 GHz, 20 MHz/ch, 12-bit, Ethernet streaming) as the passive radar
unit. TX must stay permanently disabled (receive-only project). The
telescopic ANT500 antenna (75 MHz–1 GHz) covers FM and DVB-T illuminator
bands and will be the antenna for illuminator work.

## Current phase: P0 — bench characterization (RF first)

Concrete goals, in order:

1. Capture DJI O4 OFDM signature in 5.1–5.8 GHz. NOTE: in the EU, DJI O4
   operates in **5.170–5.250 GHz** as well as 5.8 — scan both. Features to
   extract: bandwidth, duty cycle, spectral flatness, burst structure.
2. Capture ELRS chirp signature at 2.4 GHz (Boxer at fixed power, e.g.
   100 mW). Chirps appear as diagonal ridges in spectrograms; ELRS hops
   pseudo-randomly across the band per packet.
3. Walk-test range curves: SNR vs distance at multiple TX powers
   (25/100 mW) at known distances (50/100/200/400 m). Fit path-loss model
   (free-space: FSPL = 20·log10(d) + 20·log10(f) − 147.55 with f in Hz,
   d in m — CHECK any FSPL formula against this before using it; we already
   found public code with the sign of the 147.55 term inverted).
4. Record labeled IQ clips: 2–5 s blocks, cs8 format from HackRF
   (`hackrf_transfer -r file.cs8 -f <center> -s 20e6 -l 32 -g 32`),
   ~200 MB per 5 s at 20 Msps. Label everything in the manifest.

## Repo conventions

```
src/tacet/
├── loaders/    # cs8 (HackRF) / cs16 (Pluto+) / float32-interleaved (BladeRF)
│               # → complex64, strict manifest ingestion, public dataset loaders
├── dsp/        # feature extraction, detectors (chirp, OFDM, energy)
├── trackers/   # Stone Soup integration, measurement models (later)
└── nodes/      # host-side node orchestration (later; ESP32/Pi firmware will
                # live outside the Python package)
tests/          # synthetic self-tests, import tacet.* (pytest or python -m)
notebooks/      # numbered, reproducible experiments (01_bench_overview, ...)
docs/           # experiment reports
data/           # gitignored; manifest + checksums tracked in git
manifest.schema.json  # recording data contract
manifest.json   # recording index (tracked in git)
```

- uv toolchain: `uv run` / `uv add`, uv.lock committed. Python 3.13+
  (.python-version), numpy/scipy/matplotlib/pandas. pyadi-iio only as the
  `acquisition` extra (`uv sync --extra acquisition`). No heavy frameworks
  until needed. Notebooks: one question per notebook, outputs reproducible.
- Code imports the installed package (`from tacet.dsp import coherence`);
  notebooks and tests run against the project venv.
- MIT license. Public repo from day one (proof-of-prior-work matters).
- **Clean-room rule**: another GitHub project (`arall/sigint`, a SIGINT
  mesh framework) has NO LICENSE. We may take architecture *ideas* (hybrid
  autonomous nodes + central orchestration, SQLite DET logging, RSSI
  calibration with Huber regression, ATAK/CoT export) but MUST NOT copy
  any of its code. A license request issue is open; if it gets MIT/Apache
  we may build on it, until then: zero code reuse.
- A public `uav_detector.py` was reviewed and REJECTED (FSPL sign error,
  Nyquist violation in CSI processing, beacon heuristics that flag every
  WiFi router). Do not resurrect its logic. Only salvageable idea: DJI/Parrot
  OUI prefix lists for the WiFi layer.

## Open-source & data governance model (read before structuring anything)

This repo is the open research core of a broader system. It is deliberately
public and MIT-licensed: reference implementation for low-cost passive drone
detection (benchmarks, honest measurements, community contributions).

### What belongs here vs what never enters this repo

| Belongs here (public) | NEVER goes in this repo |
|---|---|
| Detection/DSP algorithms, loaders, trackers | Trained model weights |
| Bench notebooks, ROC curves, range measurements | Client or deployment data |
| Manifest schema + data governance tooling | Site calibrations |
| Node firmware prototypes | Deployment/provisioning code |
| Documentation of experiments | Business docs, pricing, strategy |

If a change requires any of the right column, stop and flag it.

### Data governance rules (enforced in code)

- Every recording has governance fields in the manifest: `contributor`,
  `source`, `consent`, `site_anonymized`. No entry without them.
- Sharing recordings with the community dataset is opt-in only via an
  explicit user command. No background telemetry, no silent uploads. If a
  module wants network access, it must ask.
- Community contributions are credited in the manifest and stay under the
  license recorded in `consent`.
- Any client-derived data must be anonymized before storage: strip site
  coordinates, client identifiers, anything linkable to a person or site.
- Public dataset loaders record provenance per file and respect their
  licenses.

### Repo hygiene

- This repo is also a credibility artifact: prefer fewer, well-measured
  claims over many unverified ones. If it wasn't measured in a notebook in
  `notebooks/`, it doesn't exist.
- External contributions welcome via normal PRs; no CLA.

## Data strategy

- Public datasets for pipeline validation only:
  - **DroneDetect** (IEEE DataPort, DOI 10.21227/5jjj-1m32): 7 consumer
    drones, 2.4375 GHz center, 28 MHz BW, float32 interleaved .dat,
    1.2e8 complex samples per file (2 s @ 60 Msps... per their spec),
    subsets: clean/BT/WiFi/both interference, modes ON/HO/FY. Filename
    pattern: `<DRONE><II><MM><NN>.dat` (e.g. MIN_1100_00). Includes
    load_data.py. Check its license before any commercial training use.
  - **DroneRF**: 43 GB, 227 RF segments, 3 drones, GitHub code available.
- **Own dataset** (the moat): labeled recordings of modern FPV links
  (ELRS, DJI O4) at known distance/power/environment. Manifest fields per
  recording: timestamp, device (drone/radio), protocol, band, center_freq,
  sample_rate, format, tx_power (if known), distance_m, environment
  (urban/rural/open), LOS/NLOS, antenna, gain settings (lna/vga), operator
  notes. No public dataset covers these signals — treat the data pipeline
  as a first-class deliverable.

## Working style expected

- Incremental, small commits, conventional messages.
- No fabricated physics: verify formulas (FSPL, Nyquist, bandwidths —
  analog FPV video is ~8 MHz, NOT 40 MHz), verify band plans, cite the
  check when in doubt. Prefer "I need to measure this" over inventing a
  threshold.
- Detection code must be testable against real recorded files, not just
  synthetic signals. Add synthetic-signal unit tests for DSP primitives
  (e.g., generate a known chirp, assert the detector finds it).
- Ask before: adding dependencies, changing repo structure, touching
  anything outside the current phase's scope.
- Current phase is P0/P1 ONLY. Passive radar and acoustic are future
  phases — do not scaffold them yet beyond the folder placeholders.
