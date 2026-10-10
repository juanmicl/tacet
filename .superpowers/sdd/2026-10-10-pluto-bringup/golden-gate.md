# Golden gate — dry-run stdout (Task 1 refactor, commit 0f0f0b7)

The three P0 dry-run commands' stdout must stay identical across the
backend extraction (and future device work that claims to preserve
behavior). `--list` output is NOT part of the gate (it changes by design
in Task 3).

## Why the mask exists

The dry-run entry JSON embeds a wall-clock timestamp
(`"timestamp": "...+00:00"`) and a session-named path/argv/id
(`data/signature_capture/<YYYYMMDDTHHMMSSZ>/...`). Raw stdout therefore
differs between ANY two runs in exactly those fields (proven with a
before-only self-diff on 2026-10-10). The gate is: raw diffs may contain
ONLY those clock fields; the masked (normalized) diff must be EMPTY.

## Persisted goldens (captured BEFORE the refactor, HEAD d7ac33f)

- `golden_dryrun_elrs.txt` / `golden_dryrun_elrs_norm.txt`
- `golden_dryrun_control.txt` / `golden_dryrun_control_norm.txt`
- `golden_dryrun_o4fixed.txt` / `golden_dryrun_o4fixed_norm.txt`

(`_norm` = raw with the mask below applied.)

## Re-run procedure (from the repo root)

```bash
cd /home/jm/repositorios/juanmicl/umbra
G=.superpowers/sdd/2026-10-10-pluto-bringup

uv run tacet capture elrs-bench --dry-run --label elrs --condition bench > /tmp/opencode/gate_elrs.txt
uv run tacet capture control --freq-mhz 2440 --dry-run --label background --condition tx_off > /tmp/opencode/gate_control.txt
uv run tacet capture o4-fixed --freq-mhz 5800 --dry-run --label dji_o4 --condition bench > /tmp/opencode/gate_o4fixed.txt

MASK='s/[0-9]{8}T[0-9]{6}Z/<STAMP>/g; s/[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\+00:00/<ISOTIMESTAMP>/g'

for s in elrs control o4fixed; do
  sed -E "$MASK" /tmp/opencode/gate_$s.txt > /tmp/opencode/gate_${s}_norm.txt
  diff $G/golden_dryrun_${s}_norm.txt /tmp/opencode/gate_${s}_norm.txt && echo "GATE $s: CLEAN"
done
```

Expected: `GATE elrs: CLEAN`, `GATE control: CLEAN`,
`GATE o4fixed: CLEAN` (diff exits 0, no output). Any other diff output is
a gate failure — do not weaken the gate or regenerate goldens to make it
pass; investigate instead.
