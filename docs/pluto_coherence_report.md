# Pluto+ inter-channel phase coherence

Status: method frozen, code and self-tests in place. **Results pending the
bench session**; every number below is a placeholder to be filled from
`notebooks/02_pluto_coherence.ipynb`.

## Goal

Measure the phase stability between the two Pluto+ RX channels and extract
the maximum coherent processing interval (CPI) we may use in passive-radar
cross-ambiguity processing: the longest window for which the inter-channel
phase difference φ12 stays within a 10° standard deviation. This number
gates phase P3 (passive radar experiments); it must be measured, not
assumed.

## Why the measurement is valid

- Both channels live in one AD9363: same LO, same sample clock. Whatever
  φ12 wander remains comes from the two RX signal paths (analog frontend,
  PCB traces, connector phase mismatch), which is exactly what bounds a
  bistatic pair built on RX1 + RX2.
- The target is the strongest local FM broadcast station (88-108 MHz).
  FM carriers are constant-envelope and continuously present in Spain, so
  the splitter feeds both channels the same signal and φ12 measures the
  receiver difference, not the signal.
- AGC is disabled on both channels (manual mode, identical gain values).
  AGC gain steps would inject phase and amplitude discontinuities that have
  nothing to do with coherence.
- One antenna, one 2-way 50 Ω SMA splitter, two same-length SMA cables:
  no transmitter is involved at any point.

## Capture plan

- 10 × 60 s chunks plus one continuous 600 s run.
- Both channels at the same center frequency (strongest FM station after a
  band scan), 2.56 Msps, saved as cs16 pairs under `data/coherence_cal/`.
- Every recording is indexed in `manifest.json` with device, protocol
  (`fm_carrier`), purpose (`coherence_cal`), gains, antenna and RF chain
  (splitter, cable length), plus sha256 per channel file.

## Analysis pipeline (`tacet.dsp.coherence`)

1. Carrier extraction: downconvert the FM carrier, ~3 kHz narrowband FIR,
   decimate to ~5 ksps (block-streaming, filter state carried across
   blocks).
2. Inter-channel phase: φ12(t) = unwrap(angle(z1 · conj(z2))).
3. Metrics per window length (10 / 50 / 100 / 500 / 1000 ms and longer
   when the capture allows): mean and std of φ12 within windows; drift rate
   from a linear fit over the long run; overlapping Allan deviation of φ12
   (oscillator-style stability vs averaging time).
4. Integer sample offset between channels: FFT cross-correlation of the
   raw streams; must be constant across recordings.
5. Per-channel calibration snapshot: DC offset, IQ amplitude imbalance and
   quadrature skew from the strong tone, noise floor difference; stored in
   `data/calibration/pluto_plus.json`.

All primitives have synthetic self-tests (`uv run python -m
tests.test_coherence`): two phase-locked tones with known offset, drift,
integer lag and IQ impairment are generated and the analysis must recover
them within tolerance.

## Results (fill after the bench session)

| Window [ms] | n_windows | mean φ12 [deg] | std φ12 [deg] |
| --- | --- | --- | --- |
| 10 | ___ | ___ | ___ |
| 50 | ___ | ___ | ___ |
| 100 | ___ | ___ | ___ |
| 500 | ___ | ___ | ___ |
| 1000 | ___ | ___ | ___ |

- Drift rate (600 s run): ___ deg/min (± ___).
- Allan deviation curve: see notebook plot (log-log, rad vs s).
- Integer sample offset: ___ samples, constant across recordings: Y/N.
- Calibration snapshot: `data/calibration/pluto_plus.json`.

**Headline: max window length with std(φ12) < 10°: ___ ms.**

## Interpretation guide

DVB-T passive radar typically uses 10-100 ms CPIs. If the 10° bound holds
for 100 ms or more, P3 is comfortable. If it holds only for shorter
windows, the honest number is still the deliverable: shorter CPIs cost
processing gain, and options such as phase-reference recalibration (the
direct-path signal can track φ12) become part of the P3 design instead of
being assumed away.

## Safety

The Pluto+ TX channels stay permanently disabled. This project is
receive-only by design and by EU legal constraint; the acquisition code
only ever enables RX channels.

## Reproducing

Run `notebooks/02_pluto_coherence.ipynb` against the project venv. Without
captures it executes top-to-bottom on clearly labeled synthetic fallback
data (pipeline demonstration only; those numbers are not measurements). At
the bench, set `RUN_CAPTURE = True` with `uv sync --extra acquisition`.
