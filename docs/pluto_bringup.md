# Pluto+ bring-up checklist

Bench document for the Pluto+ (AD9363) capture node on the tacet host.
Everything marked verified was exercised live on 2026-10-10; findings
marked empirical came out of that session's ledger. The capture backend
this document drives is `src/tacet/nodes/backends/pluto.py` (receive
only: the code mutes both TX channels at -89.75 dB on every device open
and never touches the TX path).

## Host prerequisites

The pylibiio wheels do not bundle the C library; pyadi-iio needs the
system libiio. Verified empirically on 2026-10-10:

```bash
sudo apt install libiio0 libiio-utils
uv sync --extra acquisition
```

Sanity check: `iio_info` lists four iio devices when the Pluto+ is
connected, and `uv run python -c "import adi"` imports cleanly.

## Firmware

Boot source is SPI flash by default; no microSD is needed (the slot is
only for alternative images or recovery). The unit under test runs
`v0.32-1-g7bdc-dirty` (a Pluto+ fork era image).

Before any future flash, read the current state and check the board
revision:

1. Read `fw_version` and `hw_model` over SSH (see below) or from
   `info.html` served by the device.
2. Physical gate: inspect the silkscreen in the JTAG area. `1V8` means
   Rev 1, `3V3` means Rev 2. v0.33-era images brick Rev 2 boards, so
   this check is mandatory before flashing anything.

This unit is pending the silkscreen check; do not flash anything until
it is done.

Two RX, two TX mode (needed for coherent dual RX) is driven by two
firmware environment variables:

```bash
fw_setenv compatible ad9361
fw_setenv mode 2r2t
reboot
```

On this unit 2R2T was already active (seller preconfigured, verified
2026-10-10: `compatible=ad9361`, `mode=2r2t`). Re-check with
`fw_printenv compatible mode` after any firmware change.

## Connection

Three lines to the host:

- dedicated 5 V supply, at least 2 A, on the power port. USB-only power
  causes documented brownouts;
- USB cable for data (RNDIS gadget, falls back address 192.168.2.10);
- direct Ethernet, PC to Pluto, for the capture link (GbE negotiated at
  1000 Mb/s, verified 2026-10-10).

## Network recovery

The Ethernet link is temperamental. Working configuration, all
empirical from the 2026-10-10 session:

- give NetworkManager no ideas: `nmcli dev set <if> managed no` (the
  bench interface was `enp6s0`);
- static addresses: 169.254.2.10 on the host, 169.254.2.35 on the
  Pluto. avahi link-local addressing is unstable across interface
  events, so do not rely on it;
- if `ip link` shows NO-CARRIER on either side, bounce the interface on
  both sides to establish the carrier:
  `ip link set <if> down && ip link set <if> up`.

USB drop and re-enumeration (ledgered 2026-10-10, about 12:40): the
board vanished from the USB bus (RNDIS unregister, re-enumerated as a
new device) and GbE went down with it. Recovery: replug the USB cable;
power-cycle from the 5 V supply if the device does not come back. The
capture CLI behaved correctly during the event: it failed with a clean
error, leaving no files and no manifest entry.

## SSH

The on-board dropbear (v2019.78) lacks ed25519 support; ed25519 keys
are rejected. Confirmed empirically. Generate a dedicated ECDSA key and
install it for the `root` / `analog` account:

```bash
ssh-keygen -t ecdsa -f ~/.ssh/id_ecdsa_pluto
ssh-copy-id -i ~/.ssh/id_ecdsa_pluto root@169.254.2.35
```

Passwordless SSH was verified 2026-10-10.

## TX-off physical procedure

The project is receive only. Physical and code layers, both always on:

- leave both TX SMA ports unconnected;
- the backend mutes both TX channels at -89.75 dB before any other
  configuration write, on every device open (`open_pluto` in
  `src/tacet/nodes/backends/pluto.py`).

Optional empirical check: with a HackRF listening on the Pluto's center
frequency, no transmissions should appear above the noise floor.

## First-capture smoke

Dry run first. It touches nothing (no device open, no files) and prints
the plan plus the manifest entry:

```bash
uv run tacet capture control --device pluto --uri ip:169.254.2.35 \
  --freq-mhz 98.5 --duration-s 1.0 --gain-db 30 \
  --label background --condition tx_off --dry-run
```

Verified output (2026-10-10, key lines):

```
pluto plan (dry-run): uri=ip:169.254.2.35 center=98.5 MHz rate=2.56 Msps gain=30 dB dual=False duration=1 s
config: tx muted at -89.75 dB both channels (receive-only)
"band": "VHF FM broadcast",
```

Then the real 1 s control capture (same command without `--dry-run`).
It writes one cs16 file plus a `.json` sidecar under
`data/signature_capture/<UTC session>/` and appends the entry to
`manifest.json`.

Rail-free check for Pluto cs16 files: no sample may sit at the int16
extremes. The fraction of samples with `|I| >= 32767` or `|Q| >= 32767`
must be 0.0. One-liner through the project loader:

```bash
uv run python -c "
from pathlib import Path
import numpy as np
from tacet.loaders.cs16 import load_cs16
p = sorted(Path('data/signature_capture').rglob('control_000.cs16'))[-1]
z = load_cs16(str(p))
frac = float(np.mean((np.abs(z.real) >= 32767) | (np.abs(z.imag) >= 32767)))
print(p, 'railing fraction:', frac)
assert frac == 0.0, 'capture is railing: reduce --gain-db'
"
```

If it rails, drop `--gain-db` and repeat.

## Coherence session

Hardware chain, matching `notebooks/02`:

ANT500 telescopic antenna, into a 2-way 50 ohm splitter, into RX1 and
RX2 (both Pluto RX inputs get the same FM broadcast carrier).

Scan for the strongest carrier first:

```bash
uv run tacet capture coherence-cal --device pluto --uri ip:169.254.2.35 \
  --gain-db 40 --label interference --condition bench \
  --scan-fm --dry-run
```

The scan sweeps 88.0 to 107.8 MHz in 200 kHz bins (exclusive at
108 MHz, matching the band map), discards the first 0.1 s of each bin
for PLL settling, and the winning bin is re-checked against the band
map before the capture begins. Remove `--dry-run` on session day.
Without `--scan-fm`, coherence-cal records at the default 98.5 MHz.

Sessions land in `data/coherence_cal/<UTC session>/` with two channel
files (`_ch1.cs16`, `_ch2.cs16`) plus a `.json` sidecar per capture
carrying the rate-guard evidence and sha256s. HackRF sidecars are plain
`.log` files; the formats differ by design. The rate guard aborts mono
captures and warns on dual captures when the achieved rate drops below
90 percent of the requested 2.56 Msps.

On session day set `RUN_CAPTURE=True` in the notebook so the capture
step actually runs.

## Label convention

FM broadcast carriers recorded for coherence calibration always take
`--label interference` (the ML class list has no FM entry; the
convention keeps sessions comparable), and `manifest.json` remains the
single index: never edit a capture's entry by hand.
