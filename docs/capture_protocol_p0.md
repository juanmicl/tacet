# P0 bench capture protocol

How to run a HackRF signature-capture session for the P0 bench phase, so
that every recording is comparable, indexed and reproducible. The capture
CLI wraps `hackrf_transfer -r` only: the HackRF never transmits.

## Safety

- **Props OFF.** Remove the propellers from any quad before it goes on the
  bench. A bench TX test with props installed is a dropped bench and a cut
  arm waiting to happen.
- **Containment.** Strap the airframe down or put it in a net/cage. Keep
  people, pets and metal out of the immediate beam of the test antennas.
- **Receive-only.** The HackRF is configured with `-r` (record) only. No
  jamming, no injection, no transmission from our side at any point — this
  is an EU legal constraint for private operators, not a preference. The
  only RF sources in a session are the Boxer and the quad, at the powers
  we are legally allowed to use.

## Pre-session checklist

- Battery charged (Boxer, quad); TX power on the Boxer configured BEFORE
  running any capture. The manifest records what you write in `--power`;
  it cannot verify the Boxer dial afterwards.
- Antenna seated: SMA finger-tight, correct band for the scenario
  (5.8/5.1 GHz whip for O4, 2.4 GHz for ELRS/control).
- HackRF on a **root-hub USB port** — not through a monitor or external
  hub. A 20 Msps cs8 stream is a sustained 40 MB/s (2 bytes/sample), right
  at the USB 2.0 limit; hubs and long cables are where samples get dropped.
- Free disk: ~40 MB/s of capture, and the CLI wants 2x that headroom per
  run before it starts. A 5 s capture is ~200 MB.
- `uv run python -m tests` green before you leave the desk.

## Gain procedure

1. Start low: `--lna 16 --vga 16`.
2. Run a 1 s probe capture of the real signal:
   `uv run tacet capture <scenario> --duration-s 1 --lna 16 --vga 16 ...`
3. Check `rail_fraction` for that capture in notebook 01 (or the
   byte-count/sidecar warnings from the CLI). The number must be ~0.
4. Raise gains (LNA in 8 dB steps, VGA in 2 dB steps) until the signal is
   clearly visible with rails still ~0. Clipping is not recoverable: above
   0.1% rails the capture is invalid, drop back and re-record.

## Scenario menu

Run `--dry-run` first for any new configuration: it prints the exact
`hackrf_transfer` argv and the manifest entry without touching hardware.

| Command | What it is for |
| --- | --- |
| `uv run tacet capture o4-scan --dwell-s 0.5` | find which 5.1/5.8 GHz bin the O4 link uses (20 bins, dwell 0.5 s each) |
| `uv run tacet capture o4-fixed --freq-mhz 5800` | record the O4 link on the scanned winner (also try 5180: EU O4 also lives at 5.1 GHz) |
| `uv run tacet capture elrs-bench --power 100 --freq-mhz 2420` | ELRS control link at the recorded TX power |
| `uv run tacet capture elrs-bench --power 100 --freq-mhz 2450` | ELRS, second ISM window |
| `uv run tacet capture elrs-bench --power 100 --freq-mhz 2465` | ELRS, third ISM window |
| `uv run tacet capture control --freq-mhz 2440` | ONE TX-OFF control capture per session |

Notes:

- ELRS hops the whole 2.4 GHz band: repeat the elrs-bench capture at
  2420 / 2450 / 2465 MHz for full ISM coverage, same power and gains, so
  the three windows are comparable.
- The TX-OFF control capture (protocol `noise`) is mandatory, one per
  session, same band and gains as the signal captures. Features are only
  trusted as a difference against it: noise, spurs and background carriers
  live in the control too.
- Every command accepts `--notes`, `--distance-m`, `--env`, `--los`,
  `--antenna`: fill them in, they land in the manifest entry and are the
  difference between data and archives.

## Post-session verification

1. `uv run python -m tests` — green (26+ self-tests).
2. Manifest entry count matches the number of captures you intended:
   `uv run python -c "from tacet.loaders import manifest as m; print(len(m.query(purpose='signature_capture')))"`.
3. Review byte-count warnings: any `operator_notes` containing
   "byte-count warning" marks a stream with dropped samples; check the
   sidecar `.log` next to the capture and plan a re-record.
4. Run `notebooks/01_bench_overview.ipynb` end-to-end against the session:
   rail QC per capture, feature table, spectrograms, TX-OFF comparison.

## Failure handling

- **Nonzero exit or KeyboardInterrupt (Ctrl-C):** the CLI deletes the
  partial capture and its sidecar log, prunes the now-empty session
  directory and writes nothing to the manifest. Exit codes: 2 on failure,
  130 on interrupt. There is never a partial capture to "rescue" —
  re-record.
- **Byte-count mismatch** (file size differs from
  2 bytes x 20 Msps x duration — possible silent USB sample drops): this
  is a WARNING, not a failure. Exit code is 0, the capture and its
  sidecar log are kept, and the manifest entry is written with the
  warning recorded in `operator_notes`. A capture with dropped samples is
  suspect: plan a re-record unless the segment you need is intact.
