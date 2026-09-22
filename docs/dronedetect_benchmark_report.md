# DroneDetect benchmark report

**Status:** measured (notebook 03, executed with outputs). Public-dataset
validation of the tacet feature set, not an own-capture measurement.

## Goal

Benchmark the measured `tacet.dsp.features` feature vector (occupied
bandwidth 99%, spectral flatness, duty cycle, mean burst length, mean gap
length) on the public DroneDetect V2 dataset, CLEAN subset, as 7-class drone
identification with one-vs-rest detection framing.

## Method

- Dataset: DroneDetect V2, doi:10.21227/5jjj-1m32, CC BY 4.0. CLEAN subset:
  7 drones x 3 modes nominal (Disco: ON/FY, no hover, fixed-wing; Phantom 4:
  ON/HO, since the release ships PHA_FY as an empty directory), 5 files per
  populated drone-mode, 2 s at 60 Msps complex float32, center 2.4375 GHz.
  19 populated directories, 95 files, windowed reads only.
- 10 windows of 100 ms per file, evenly spaced across the first 2 s
  (950 windows). Window = evaluation unit; file = split unit (files 00-02
  train / 03-04 test, no window leakage).
- Classifier: numpy softmax regression (full-batch GD, standardized
  features), guarded by a synthetic sanity cell (accuracy/AUC > 0.99).
- Provenance recorded per file by the loader (source public_dataset,
  doi, license); no manifest.json entries (label enum has no per-drone
  classes; see spec decisions).

## Results

Overall test accuracy: 0.4211 (380 test windows, 7 classes; the 7-class
chance level is ~0.143, so this is ~2.9x chance).

| drone | model | test windows | one-vs-rest AUC |
| --- | --- | --- | --- |
| AIR | DJI Air 2S | 60 | 0.740521 |
| DIS | Parrot Disco | 40 | 0.843529 |
| INS | DJI Inspire 2 | 60 | 0.729688 |
| MIN | DJI Mavic Mini | 60 | 0.978021 |
| MP1 | DJI Mavic Pro | 60 | 0.788490 |
| MP2 | DJI Mavic 2 Pro | 60 | 0.601823 |
| PHA | DJI Phantom 4 | 40 | 0.884191 |

Mean one-vs-rest AUC: 0.795180.

Per-mode AUC (ON/HO/FY). Definition: positives are that drone's test
windows in that mode; negatives are all other drones' test windows, so the
same drone's other-mode windows sit out both sides. NaN = mode absent for
that drone. Each cell rests on 20 test windows (2 test files x 10
windows); read them as indicative only.

```
mode         FY        HO        ON
drone
AIR    0.827812  0.816562  0.577187
DIS    0.813676       NaN  0.873382
INS    0.644375  0.741094  0.803594
MIN    0.967656  0.985469  0.980938
MP1    0.718906  0.809375  0.837187
MP2    0.333750  0.609219  0.862500
PHA         NaN  0.797206  0.971176
```

Confusion summary: MIN (DJI Mavic Mini) is nearly perfectly separable at
57/60 windows and AUC 0.978, strong across all modes (0.968-0.985), with a
distinct burst-timing signature (shortest mean bursts and gaps, narrowest
occupied bandwidth). MP2 (DJI Mavic 2 Pro) is the weakest class: 10/60
correct, AUC 0.602, confused mainly with AIR (21 windows) and MP1 (17
windows), the same DJI family and link technology. DIS (Parrot Disco)
and INS (DJI Inspire 2) confuse mutually (DIS→INS 12, INS→DIS 4). AIR
absorbs many false positives (largest off-diagonal column: MP2 21, MP1 17,
PHA 14, INS 13) while its own true row leaks most to DIS (23). PHA is
strong in ON mode (0.971) but dragged down by HO (0.797) and cross-family
leaks to AIR (14) and INS (9).

## Honest-results note

- No background/no-UAS class exists in the published DroneDetect V2; the
  paper's 2-class detection used unpublished drone-absent recordings. Our
  numbers are therefore not comparable to Swinney & Woods' 8-class 98.1%.
- Window-level evaluation on 100 ms units; windows within a file are
  correlated. File-level split prevents train/test leakage.
- Features were designed for bench ELRS/O4 characterization (20 Msps cs8
  pipeline); this is a cross-dataset transfer, not a tuned benchmark.
- Endianness of the .dat files is not officially documented; the notebook
  sanity cell verifies size % 8 == 0, plausible sample statistics and a
  visible PSD, consistent with little-endian float32 interleaved IQ.

## Interpretation guide

- AUC per drone answers: how separable is this drone from all others using
  our 5 features alone. In this run the spread runs from MIN (AUC 0.978,
  57/60) down to MP2 (AUC 0.602, 10/60, barely above the 0.5 one-vs-rest
  chance level), so the 5 features carry real per-drone information but
  do not separate drones within the same DJI link family (MP2 ↔ AIR/MP1).
- Per-mode differences (if any) point at flight-mode-dependent signal
  structure rather than a fixed per-drone fingerprint: MP2 collapses in
  FY (0.334) while ON is 0.863, PHA falls from 0.971 (ON) to 0.797 (HO),
  and MIN alone stays high across all its modes (0.968-0.985).

## Safety

Receive-only analysis of a public dataset. No transmission, interception
of content, or neutralization capability is involved or implied.

## Reproducing

```bash
# dataset (CC BY 4.0, IEEE DataPort login): doi:10.21227/5jjj-1m32
# extract DroneDetect_V2.zip so that data/datasets/dronedetect_v2/CLEAN/
# contains the <CODE>_<MODE>/ directories
uv run python -m tests
uv run python scripts/execute_notebook.py notebooks/03_dronedetect_benchmark.ipynb
```

## Dataset citation

Swinney, C.J.; Woods, J.C., "DroneDetect Dataset: A Radio Frequency dataset
of UAS Signals for Machine Learning Detection & Classification," IEEE
DataPort, June 12, 2021, doi:10.21227/5jjj-1m32 (CC BY 4.0). Reference
results: Swinney & Woods, Aerospace 2021, 8(7):179, doi:10.3390/aerospace8070179.
