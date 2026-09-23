# ELRS detector report

Status: measured (notebook 04, executed with outputs). First tacet
detector; bench-only validation.

## Goal

Detect the ELRS 2.4 GHz control link per 100 ms block with a data-driven
CFAR threshold, attribute detections as hopping (ELRS-like) vs stationary
(WiFi-like), and measure detection probability vs effective SNR on the
bench captures.

## Method

- CFAR: per-time-slice median noise floor over eligible spectrogram bins
  (DC excluded); bin threshold gamma = -ln(pfa)/ln(2) = 6.64 at pfa 0.01,
  because the floor is a median of an exponential bin-power distribution
  (median = mean * ln(2), so P(power > gamma * floor) = exp(-gamma*ln 2)).
  Per-bin pfa 0.01 for the presence matrix and 1e-4 for the attribution
  matrix: at 0.01 the raw detection pattern saturates on noise, so
  hopping structure is only readable at the stricter operating point.
- Block statistics, one dict per 100 ms block:
  max_occupied_fraction (largest per-slice fraction of eligible bins
  above threshold; on pure noise each slice flags about 1% of bins, and
  the block max over roughly 2000 slices lands around 0.02 to 0.04,
  measured 0.028 to 0.037 in the unit tests), excess_db (largest
  bin-power-to-floor ratio in dB, DC rows excluded; on noise it is the
  extreme of exponential draws against the median floor and is positive
  by definition), bin_diversity_attr (fraction of frequency bins flagged
  anywhere in the block on the 1e-4 matrix; sparse on noise, spread
  across the band for a hopping link) and turnover_attr (mean Jaccard
  distance between the detected bin sets of consecutive slices; high
  when occupancy moves between slices, low for a persistent stationary
  carrier).
- Validation: synthetic noise (model check), 5 usable controls (bin
  exceedance upper bounds; the saturated 32/32 control
  20260919T003427Z, 3.9% rails, excluded by rail QC), 8 ELRS captures
  (bench ROC), and a signal+control mixing ladder with the scale factor
  alpha solved per pair for measured SNR_eff targets -3, -6, -9, -12 and
  -15 dB.
- Two-level Pfa: the bin-level pfa feeds the detection matrix; the
  block-level threshold is the (1 - 0.01) quantile of the 700 pooled
  control blocks.

## Results

- Model check (synthetic noise, independent seed): empirical bin-Pfa
  0.01025 vs nominal 0.01 (within the 4-sigma band, half width 0.00096).
- Control exceedance (bin-level exceedance is an upper bound on the
  field bin-Pfa: ambient WiFi genuinely occupies bins, and without
  ground truth real occupancy and model error are indistinguishable):

  | control | blocks | bin_exceedance | median_max_occ | p99_max_occ | median_excess_db |
  | --- | --- | --- | --- | --- | --- |
  | 20260919T003513Z_control_000 | 10 | 0.031508 | 0.219212 | 0.245232 | 62.118502 |
  | 20260919T005401Z_control_000 | 30 | 0.010334 | 0.077833 | 0.166798 | 47.818480 |
  | 20260919T005404Z_control_000 | 30 | 0.016286 | 0.181281 | 0.192532 | 21.621102 |
  | 20260919T005408Z_control_000 | 30 | 0.019454 | 0.204433 | 0.226128 | 22.207478 |
  | 20260921T190315Z_control_000 | 600 | 0.033808 | 0.213300 | 0.255172 | 59.612362 |

- Bench ROC (8 ELRS captures, 1950 signal blocks, vs 5 controls, 700
  control blocks): AUC 0.6686 (max_occupied_fraction), AUC 0.4557
  (excess_db); block threshold max_occupied_fraction > 0.2542 at
  block-Pfa 1%; Pd at that threshold 0.0636. The excess_db AUC sits
  below chance (0.4557), consistent with the ambient-WiFi-dominated
  null producing strong per-bin excursions (a point estimate; the
  capture-level clustering noted with the attribution results puts
  sub-0.5 values like this within noise of chance); only the
  control-side medians were tabulated, so no directional ordering
  between the two sides is claimed.
- Mixing ladder (500 mixed blocks, 100 per rung, alpha solved per pair;
  per-pair bench per-bin SNR +21.0, +11.3, +10.1 and +14.8 dB, clip
  floored at zero so these are upward-biased estimates, not calibrated
  SNR values). Pd vs SNR_eff at block-Pfa 1%, null 700 pooled control
  blocks, threshold max_occupied_fraction > 0.2542:

  | snr_eff_db | blocks | pd_at_pfa1pct | auc_vs_control_null |
  | --- | --- | --- | --- |
  | -15 | 100 | 0.00 | 0.310764 |
  | -12 | 100 | 0.00 | 0.314600 |
  | -9 | 100 | 0.00 | 0.316671 |
  | -6 | 100 | 0.00 | 0.316950 |
  | -3 | 100 | 0.01 | 0.314450 |

  No rung approaches Pd 0.9. The statistic is nearly flat over the
  ladder (mean max_occupied_fraction 0.1623 at -15 dB to 0.1657 at
  -3 dB), and even bench-level blocks only reach Pd 0.0636. The
  auc_vs_control_null column is P(mixed block > control block) with
  ties counted 0.5, against the same 700-block null the threshold
  derives from; every rung lands near 0.31, below chance.
- Attribution (ELRS blocks vs control blocks, pfa 1e-4 matrix): AUC
  0.7112 (bin_diversity_attr), AUC 0.4376 (turnover_attr). Both carry
  clustered-block uncertainty: 92% of the positive blocks come from
  three 60 s captures and 86% of the negative blocks from a single
  capture, so the effective sample is captures, not blocks. Scatter
  separation is poor: large overlap, with ELRS blocks spanning a broad
  mid-to-high bin_diversity band (about 0.4 to 0.95) while controls
  cluster at low diversity or at mid diversity with near-maximal
  turnover, skewing toward higher turnover than ELRS, which is why
  turnover_attr lands below chance, at 0.4376, a mildly anti-selective
  point estimate on this data (capture-level clustering, see below).

## Honest-results note

- Mixing adds two recorded noise realizations and scales the signal
  digitally: it does not reproduce a weaker signal at the antenna
  (noise figure and AGC effects are not modeled; gains were manual and
  matched).
- SNR_eff is measured per pair from PSD differences, not assumed from
  the scale factor alpha; the floor rise (about 3 dB at alpha = 1) is
  inherent to the method and is why the ladder starts below bench SNR.
- Bench-only distances (about 1 m): no range claim until the
  walk-test.
- The block threshold and Pfa rest on the pooled control blocks (counts
  in Results), 86% of them from one 60 s capture whose tail sets the
  0.2542 threshold: small-null quantiles are coarse at the 1% level.
- D500 packet timing is unverified (out of scope); the ambient WiFi in
  the controls is whatever was in the air that day, not a controlled
  source.
- The controls table reports exceedance rates, not model error: real
  occupancy and model error are indistinguishable without ground
  truth.

## Interpretation guide

On this statistic and this data (bench captures, about 1 m,
occupancy-dominated null) the detector is not usable as a standalone
block detector: it does not reach Pd 0.9 at any measured rung, Pd stays
at 0.00 to 0.01 from -15 to -3 dB SNR_eff, and even bench-level blocks
reach only Pd 0.0636 at the 1% block threshold. One
confound bounds what the ladder can show: the mixed positives carry
their pair controls' ambient WiFi at full strength against a null
dominated by one occupancy-heavy capture, so the flat Pd is partly a
floor effect of an occupancy statistic evaluated against an
occupancy-dominated null. The measured lesson is that this statistic
cannot separate ELRS from heavy ambient WiFi at these SNRs, not that
ELRS is undetectable. Attribution does not rescue it on this data:
bin_diversity_attr is weak-moderate (0.7112) and turnover_attr is below
chance (0.4376), both subject to the capture-level clustering caveat
noted in Results.

## Safety

Receive-only analysis of own bench captures. No transmission,
interception of content, or neutralization capability.

## Reproducing

Requires the P0 bench captures listed in manifest.json (own recordings
under data/signature_capture/, not distributed with the repo).

```bash
uv run python -m tests
uv run python scripts/execute_notebook.py notebooks/04_elrs_detector_roc.ipynb
```

## Measurement basis

docs/p0_signature_report.md (bench characterization, matched controls).
