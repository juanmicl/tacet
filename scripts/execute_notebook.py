#!/usr/bin/env python3
"""Execute a notebook headless and write it back WITH outputs.

Repo tooling for the "commit notebooks with outputs" rule (numbers that
are not committed do not exist publicly). Runs every code cell in order
in one namespace under matplotlib's Agg backend, captures stdout/stderr
as stream outputs, captures open figures as PNG display_data outputs,
stamps sequential execution_count values and writes the notebook in
place. Exits nonzero (after writing the partially executed notebook) if
any cell raises, so CI/verification can gate on it.

Usage:
    uv run python scripts/execute_notebook.py notebooks/01_bench_overview.ipynb
"""

import argparse
import base64
import contextlib
import io
import json
import sys
import traceback
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402  (backend must be set first)


def _figure_outputs() -> list:
    """Capture every open figure as a PNG display_data output."""
    outputs = []
    for fignum in plt.get_fignums():
        fig = plt.figure(fignum)
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        outputs.append({
            "output_type": "display_data",
            "data": {"image/png":
                     base64.b64encode(buf.getvalue()).decode("ascii")},
            "metadata": {},
        })
        plt.close(fig)
    return outputs


def _stream_output(name: str, text: str) -> dict | None:
    if text:
        return {"output_type": "stream", "name": name, "text": text}
    return None


def execute_notebook(nb: dict) -> bool:
    """Execute nb in place; return True iff every code cell succeeded."""
    ns: dict = {}
    count = 0
    all_ok = True
    for cell in nb["cells"]:
        if cell["cell_type"] != "code":
            continue
        count += 1
        cell["execution_count"] = count
        cell["outputs"] = []
        out, err = io.StringIO(), io.StringIO()
        cell_ok = True
        try:
            code = cell["source"]
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                exec(compile(code, f"<cell {count}>", "exec"), ns)  # noqa: S102
        except Exception as exc:  # noqa: BLE001 - reported as cell output
            cell_ok = False
            all_ok = False
            cell["outputs"].append({
                "output_type": "error",
                "ename": type(exc).__name__,
                "evalue": str(exc),
                "traceback": traceback.format_exc().splitlines(),
            })
        cell["outputs"].append(_stream_output("stdout", out.getvalue()))
        cell["outputs"].append(_stream_output("stderr", err.getvalue()))
        cell["outputs"].extend(_figure_outputs())
        cell["outputs"] = [o for o in cell["outputs"] if o is not None]
        status = "ok" if cell_ok else "ERROR"
        n_png = sum(1 for o in cell["outputs"]
                    if o["output_type"] == "display_data")
        print(f"cell {count}: {status} "
              f"(stdout {len(out.getvalue())} B, "
              f"stderr {len(err.getvalue())} B, {n_png} figure(s))")
    return all_ok


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Execute a notebook headless and write it with outputs.")
    parser.add_argument("notebook", help="path to the .ipynb file")
    args = parser.parse_args(argv)

    path = Path(args.notebook)
    nb = json.loads(path.read_text(encoding="utf-8"))
    all_ok = execute_notebook(nb)
    path.write_text(json.dumps(nb, indent=1) + "\n", encoding="utf-8")
    if not all_ok:
        print(f"{path}: executed WITH ERRORS (see the error outputs)",
              file=sys.stderr)
        return 1
    print(f"{path}: NOTEBOOK OK, outputs written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
