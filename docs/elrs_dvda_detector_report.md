# ELRS DVDA detector report

Status: measured (notebook 07, executed with outputs; final iteration at
the LO-band pairing exclusion). Honest miss: the success criterion, Pd
at least 0.9 at block-Pfa 1 percent against the pooled null, failed on
this data. Final numbers: Pd 0.0087, bench AUC 0.5015, block threshold
0.5323. What ships is real receiver hygiene (a Pfa-calibrated gamma
energy mask, adjacent-channel ghost suppression, LO-band pairing
exclusion) plus a measured diagnosis of why the channelized detector
cannot see the twin fingerprint that notebook 06 proved exists on this
same data.

## Goal

Detect the ELRS 2.4 GHz control link in D-mode (DVDA double-send) per
100 ms block with a template-free matched-structure statistic, and
evaluate it like-for-like against notebook 04: Pd at least 0.9 at
block-Pfa 1 percent against the same pooled WiFi-heavy null, plus a
mixing ladder over measured SNR_eff from -15 to -3 dB.

## Method

- DVDA background: ExpressLRS D-rates transmit every payload twice on
  the same FHSS channel about 1 ms apart (interval 1000 us, numOfSends
  2; firmware values cited from ExpressLRS, GPL-3.0, facts only). Two
  nearly identical complex envelopes 1 ms apart are the fingerprint;
  WiFi retransmissions re-scramble the PDU, so their envelopes do not
  repeat.
- Channelized architecture (src/tacet/dsp/dvda.py): each 100 ms complex
  block is split into 20 channels of 1 MHz (FFT, consecutive bin
  groups, per-group ifft back to baseband at 1 Msps). Channel k is
  centered at (k - 10 + 0.5) MHz relative to the capture center.
- Gamma-quantile mask: per channel, power is integrated into 50 us bins
  (50 samples per bin); a bin sums 50 iid exponential powers, so the
  threshold is the exact gamma quantile scaled by the block mean,
  T = gamma.ppf(1 - pfa, a=50) * mean/50 = 1.495 x mean at pfa 1e-3.
  The mask is scale-invariant: it tracks each channel's mean, so a
  constant interferer raises threshold and power together and never
  crosses (measured below). Segments are contiguous runs of bins above
  threshold.
- Pairing: same-channel segments whose start gap falls in the 0.7 to
  1.4 ms window are correlated coherently (FFT cross-correlation,
  zero-pad safe); the block statistic is max_pair_peak, the best pair
  peak over all channels and pairs.
- Receiver hygiene in detect_block: (a) ghost suppression, a segment
  that time-overlaps a strictly stronger neighbor in channel k +/- 1 is
  dropped as a spectral-leakage ghost before pairing; (b) LO-band
  exclusion, channels 9, 10 and 11 (the receiver's LO leakage spans
  roughly +/-1.5 MHz on this hardware) run the full mask and segment
  chain but their kept segments skip pairing, and they stay in the
  reference lists that ghost suppression reads; the excluded band fully
  covers 3 of the ~20 in-window FHSS raster slots (offsets -0.6, +0.4
  and +1.4 MHz) and can ghost-suppress a fourth (-1.6 MHz) when an LO
  burst overlaps it, so the max statistic is insensitive to the cost.
- LBT drop-not-defer semantics: ELRS LBT drops (not defers) a
  busy-channel send, and both DVDA sends pass LBT independently, so
  pair survival is (1 - p_busy)^2; consequences in the honest-results
  note.
- Composability contract: energy_mask is always fed the 100 ms block
  slice of a channel (its threshold derives from that block's mean);
  detect_block consumes whole blocks.
- Null and criterion, exactly notebook 04's: the 5 usable controls (the
  saturated 32/32 control 20260919T003427Z excluded by rail QC) pooled
  into 700 blocks of 100 ms; the block threshold is the (1 - 0.01)
  quantile of the null's max_pair_peak; Pd is the fraction of the 1950
  signal blocks above it; bench AUC over all blocks with ties counted
  0.5. Same capture set, same 100 ms blocks, same 1 percent
  block-quantile rule; only the block statistic changed
  (max_occupied_fraction to max_pair_peak). The mixing ladder is
  identical too: 4 signal+control pairs, alpha solved per pair for
  measured SNR_eff targets -3, -6, -9, -12 and -15 dB, 500 mixed
  blocks, 100 per rung.

## Results

### Synthetic sanity (independent seed, mirrors tests/test_dvda.py)

```
twin: n_pairs 1, peak 1.000
diff-content: peak 0.170
```

The tested invariant is the relative separation, 5.9x here.

### The fix journey (three library iterations, notebook re-executed unchanged at each)

- Initial run: threshold 0.7200, Pd 0.0031, bench AUC 0.5135; the
  ladder flat at Pd 0.000 with mean_pair_peak 0.112-0.125; the null's
  peak tail reached the 0.72 threshold while typical signal peaks sat
  near 0.12; the best-pair channel histogram dominated by the center of
  the band.
- Diagnosis, LO leakage: the dominant channel is the DC-containing
  center channel 10, with the adjacent channel 9 next (DC sits on the
  9/10 boundary, so LO leakage spills into both). The constant-DC
  hypothesis was falsified by measurement: the scale-invariant mask
   makes constant DC inert (channel 10 with a constant DC spike of
   amplitude 60: bin CV 5.2e-4, max/mean 1.0020 against a threshold
   ratio of 1.495, zero segments; slow 5 percent AM at 2 Hz, max/mean
  1.0215, still zero segments and pairs). What actually pairs is bursty
  envelope variation in the LO band: a phasor gated at 0.1-0.8 ms
  reproduces the failure signature (peak 0.993 through channel 10), and
  remove_dc leaves it intact (0.993 to 0.985, shifted to channel 9).
- Ghost suppression (red-dance verified): leakage ghosts dropped before
  pairing. In the synthetic harness a physical twin plus two ghost
  pairs (3 pairs) collapses to 1 pair; the different-content peak falls
  from 0.194 (a channel-14 ghost) to 0.141, the true channel-13
  envelope correlation.
- Center-channel exclusion (red-dance verified): channel 10 skips
  pairing, retained as suppression reference. Without the skip, gated
  leakage pairs at 0.9999 through channel 10 and dominate; with it,
  green. The notebook re-execution at this stage kept threshold 0.7200
  and Pd 0.0031 unchanged (AUC 0.5131): best_channel no longer took 10,
  but the dominance had moved to the adjacent channel 9.
- LO-band exclusion (red-dance verified, final mechanical iteration):
  channels 9-11 all skip pairing. Without the widening, a second gated
  leakage tone at -0.75 MHz pairs at 0.99997 through channel 9; with
  it, green.
- Outcome: the threshold fell 0.7200 to 0.5323, confirming the LO band
  did inflate the null tail; channels 9-11 are empty in the best-pair
  channel histogram and no dominant channel remains (mass spread over
  0-8 and 12-19, maxima near 4 and 14); mean ladder peaks collapsed
  from 0.112-0.125 to 0.031-0.043 (the LO band had been carrying the
  earlier ~0.12 peaks). Pd moved 0.0031 to 0.0087 against the 0.9
  target and AUC 0.5135 to 0.5015. The criterion stayed failed.
  Receiver hygiene was necessary but not sufficient.

### Bench verdict (final, the success criterion)

```
pooled null blocks: 700; threshold 0.5323
Pd at block-Pfa 0.01: 0.0087
bench AUC: 0.5015
before/after: occupancy Pd 0.0636 (notebook 04) -> DVDA 0.0087
```

### Mixing ladder (500 mixed blocks, 100 per rung, threshold 0.5323 from the pooled null)

| snr_eff_db | blocks | pd_at_pfa1pct | mean_peak |
| --- | --- | --- | --- |
| -15 | 100 | 0.0 | 0.039303 |
| -12 | 100 | 0.0 | 0.043215 |
| -9 | 100 | 0.0 | 0.040611 |
| -6 | 100 | 0.0 | 0.031163 |
| -3 | 100 | 0.0 | 0.032699 |

No crossing point: Pd is 0.000 at every rung and mean_pair_peak is flat
(0.031-0.043) from -15 to -3 dB, so the statistic shows no response to
signal power across the measured range.

### Diagnostics (final run)

- Gap mode: the expected sharp ~1 ms DVDA mode is absent from
  best_pair_gap_ms even in signal blocks; the distribution is broad
  across 0.7-1.35 ms with lumps near 0.8 and 1.3 ms. This is the tell
  that the ranked candidate pairs are not the packet twins.
- Null tail: both null and signal max_pair_peak pile below 0.1 (signal
  blocks concentrate below 0.05-0.1); the null's tail reaches the
  0.5323 threshold, fed diffusely by ordinary candidate pairs across
  many channels now that the LO band is excluded. Hence AUC 0.5015.
- Channel spread: channels 9-11 empty; the remaining mass spreads over
  0-8 and 12-19 with maxima near 4 and 14. No localized leak remains.

### The twin fingerprint exists; the detector never surfaces it

Notebook 06's segmentation-guided pairing measured the fingerprint on
this same data: envelope cross-correlation peak median 0.133 (range
0.06-0.71) against a 0.022 floor, z 15.5, 200/200 pairs. The per-sample
SNR at this bandwidth is about 0 dB (notebook 06's tone variance ratio
1.09), so the real fingerprint cannot rise above the per-block maximum
over the many candidate pairs the null provides.

### Before/after across both detector attempts

| architecture | statistic | source | Pd at block-Pfa 1 percent | threshold | bench AUC |
| --- | --- | --- | --- | --- | --- |
| occupancy (spectral) | max_occupied_fraction | notebook 04 | 0.0636 | 0.2542 | 0.6686 |
| channelized DVDA | max_pair_peak | notebook 07 (final) | 0.0087 | 0.5323 | 0.5015 |

Two architectures against the same pooled null, two honest misses with
different mechanisms. The occupancy statistic was beaten by the null's
own occupancy; the DVDA statistic is beaten by candidate generation
that never isolates the packet twins. The twin signal itself is
measured and real (notebook 06); it is the per-block channelized
candidate generation that fails to deliver it to the correlator.

## Honest-results note

- LBT pair survival: busy sends are dropped, not deferred, and both
  DVDA sends pass LBT independently, so pair survival is (1 - p_busy)^2;
  pairs thin out in exactly the WiFi-heavy regime, partially
  self-corrected by survivor concentration on quiet channels. The
  pooled null measures this at our bench; other sites should compare
  their null's peak distribution against the one printed in the
  notebook.
- DVDA dependency: the statistic is matched to the D-mode double-send;
  an ELRS mode without double-send defeats it (occupancy statistics
  degrade differently).
- WiFi false twins: 802.11 retransmissions re-scramble the PDU, so
  complex envelopes do not repeat; the deterministic L-STF/L-LTF is
  ~16 us, below the 200 us minimum segment. A mechanism claim, backed
  empirically by this null.
- Bench-only distances (about 1 m); no range claim until the
  walk-test.
- The pooled null is dominated by one 60 s capture (600 of the 700
  blocks), so its 1 percent quantile is a coarse small-null estimate.
- Mixing adds two recorded noise realizations and scales the signal
  digitally; it does not reproduce a weaker signal at the antenna, and
  the per-pair SNR_eff values inherit notebook 04's upward bias (clip
  floored at zero).
- Block-boundary pairs are lost (about 1.4 percent of candidate pairs
  straddle block edges).
- Template-free: no per-link identification in this build.
- The synthetic different-content margin is modest (0.141 against the
  0.15 test gate); any future SNR-robbing change to the harness should
  re-check it.

## Interpretation guide

The criterion was not reached. Pd is 0.0087 at block-Pfa 1 percent
(target 0.9), bench AUC 0.5015 is chance level, and the ladder crosses
Pd 0.9 at no measured SNR: it is flat at 0.000 from -15 to -3 dB. On
this statistic and this data the channelized DVDA detector is not
usable as a standalone block detector, and the measured diagnosis says
why: the channelized gamma-mask candidate generation does not isolate
ELRS packets. The twin fingerprint exists on this same data (0.133
median against a 0.022 floor, z 15.5, 200/200 pairs, notebook 06), but
the detector's per-block candidates never surface it: no ~1 ms gap mode
appears even in signal blocks, and the null tail is fed diffusely by
ordinary candidate pairs. This is the binding limitation for any next
attempt: promote notebook 06's segmentation-guided twin mining into the
detector (segmentation-guided pairing as the candidate generator, the
correlator and Pfa discipline unchanged) instead of ranking
channel-derived segment pairs.

The shipped library contributions stand on their own and are receiver
hygiene any future detector on this hardware needs: the Pfa-calibrated
gamma energy mask (exact quantiles, scale-invariant), ghost
suppression, and the LO-band pairing exclusion (9/9 module tests, 95/95
suite). They reduced the null tail exactly as designed (threshold
0.7200 to 0.5323, LO channels empty) and the criterion still failed:
hygiene was necessary but not sufficient.

## Safety

Receive-only analysis of own bench captures. No transmission,
interception of content, or neutralization capability.

## Reproducing

Requires the P0 bench captures listed in manifest.json (own recordings
under data/signature_capture/, not distributed with the repo).

```bash
uv run python -m tests
uv run python scripts/execute_notebook.py notebooks/07_dvda_detector_validation.ipynb
```

## Measurement basis

docs/elrs_detector_report.md (v1 occupancy attempt), notebooks 04-07,
src/tacet/dsp/dvda.py with tests/test_dvda.py, the DVDA spec
(.opencode/superpowers/specs/2026-09-23-dvda-detector-design.md,
local). Firmware values cited from ExpressLRS (GPL-3.0), facts only.
Semtech SX1280 datasheet Rev 3.2 for the FLRC physical layer.
