#!/usr/bin/env python3
"""Phase 5 — One-command demo runner for the Document Intake QC Agent.

Runs the whole pipeline end to end, in the only order that works:

    scan -> classify -> rules -> organize x3 -> report

ORDER IS LOAD-BEARING, which is why it lives in code rather than in a README
the reader has to follow correctly:

  * rules.py reads manifest.csv, so scan.py must run first
  * rules.py folds the classification findings in only if classifications.csv
    already exists, so classify.py runs before it, not after
  * organize.py --by class reads classifications.csv
  * report.py reads ALL THREE cross-references, so every organize mode must
    have run before it. Running the report early does not fail loudly — it
    fails by reconciling against a set that is missing a third of its data.

`STEPS` below is the single source of truth for that chain. `qc_phase5.py`
imports it and asserts the README documents exactly these commands in exactly
this order, so the documentation cannot drift from what the code does.

--no-ai runs the deterministic half only (scan -> rules -> organize by author
and custodian). Phase 2 stands alone on a machine with no model installed, and
this flag is what proves it. The class axis and the report are skipped, because
both read classifications.csv and neither can honestly be produced without it.

Run:  py scripts/run_demo.py
      py scripts/run_demo.py --no-ai
      py scripts/run_demo.py --input D:\\ClientData\\Acme
--input is threaded to the two steps that read the intake (scan and organize);
the rest work from manifest.csv and never see it.
Requires: mock-intake/ by default (regenerate with `py scripts/make_mock_data.py`).
The full run also requires Ollama serving gemma4:12b on 127.0.0.1:11434 —
checked up front, so a missing model costs a second rather than a scan.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from intake import add_input_arg, resolve_intake

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
ANSWER_KEY = ROOT / "seeded-errors.json"
OLLAMA_TAGS = "http://127.0.0.1:11434/api/tags"

# The canonical pipeline. (script, args, needs_ai, what it produces)
# needs_ai is transitive: organize --by class and report.py never call the
# model themselves, but neither can run without the file classify.py writes.
STEPS: list[tuple[str, list[str], bool, str]] = [
    ("scan.py",     [],                       False, "manifest.csv"),
    ("classify.py", [],                       True,  "classifications.csv"),
    ("rules.py",    [],                       False, "exceptions.csv"),
    ("organize.py", ["--by", "class"],        True,  "organized/by-class/"),
    ("organize.py", ["--by", "author"],       False, "organized/by-author/"),
    ("organize.py", ["--by", "custodian"],    False, "organized/by-custodian/"),
    ("report.py",   [],                       True,  "qc-report.html"),
]

# The steps that accept --input. rules.py, classify.py and report.py all work
# from manifest.csv and never touch the intake themselves, so handing them the
# flag would be a lie about what they read. Kept as a set beside STEPS rather
# than a fifth tuple field so qc_phase5.py's unpacking of STEPS is unchanged.
TAKES_INPUT = {"scan.py", "organize.py"}


def command_line(script: str, args: list[str]) -> str:
    """The step as a reader would type it. Used for the console log and, via
    qc_phase5.py, for checking the README quotes these exact commands."""
    return " ".join(["py", f"scripts/{script}", *args])


def ollama_ready() -> tuple[bool, str]:
    """Ask Ollama for its model list. Returns (ok, message).

    Checked before any work starts. classify.py is 40 seconds in when it would
    otherwise discover the model is missing, and a demo that dies halfway
    leaves a half-built set of artifacts for the next reader to puzzle over.
    """
    try:
        with urllib.request.urlopen(OLLAMA_TAGS, timeout=5) as resp:
            body = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        return False, f"Ollama is not answering on {OLLAMA_TAGS} ({exc})"
    if "gemma4:12b" not in body:
        return False, "Ollama is running but gemma4:12b is not installed"
    return True, "Ollama is up, gemma4:12b present"


def seeded_dates_intact() -> list[str]:
    """Return the date-anomaly files whose planted mtime has been lost.

    Git stores no modification times, so a fresh clone checks every file out at
    clone time and the two deliberately impossible dates — 1980 and 2031 —
    quietly become today. The pipeline then finds 37 problems instead of 39 and
    is not wrong about anything; the fixture simply stopped containing the
    error. That is the worst kind of failure for a demo whose whole claim is
    "every planted error is caught", so it is checked before the run, not
    discovered afterwards by a reader comparing totals against the README.
    """
    if not ANSWER_KEY.is_file():
        return []
    key = json.loads(ANSWER_KEY.read_text(encoding="utf-8"))
    now = datetime.now().timestamp()
    floor = datetime(2000, 1, 1).timestamp()
    lost = []
    for err in key.get("seeded_errors", []):
        if err.get("type") != "date-anomaly":
            continue
        for rel in err.get("paths", []):
            path = ROOT / rel
            if path.is_file() and floor <= path.stat().st_mtime <= now:
                lost.append(rel)
    return lost


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the full document intake QC pipeline on mock-intake/.")
    parser.add_argument("--no-ai", action="store_true",
                        help="skip the model: deterministic rules and the "
                             "author/custodian axes only, no report")
    add_input_arg(parser)
    args = parser.parse_args()

    # resolve_intake reports a missing folder and exits, so the runner does not
    # need its own existence check.
    intake = resolve_intake(args.input)
    print(f"Intake: {intake}\n")

    # The date guard reads the fixture's answer key, so it only means anything
    # when we are running against the fixture. On a client intake there is no
    # answer key and nothing to compare — silence is the honest result.
    lost = seeded_dates_intact() if args.input is None else []
    if lost:
        print("WARNING: the fixture's seeded date anomalies are gone — git does "
              "not preserve modification times, so a fresh clone resets them.\n"
              "         Affected: " + ", ".join(lost) + "\n"
              "         This run will find 2 fewer problems than the README "
              "states. Restore them first:\n"
              "           py scripts/make_mock_data.py\n", file=sys.stderr)

    steps = [s for s in STEPS if not (args.no_ai and s[2])]

    if args.no_ai:
        skipped = [command_line(s, a) for s, a, needs_ai, _ in STEPS if needs_ai]
        print("--no-ai: running the deterministic pipeline only.")
        print("Skipping (each one needs classifications.csv):")
        for cmd in skipped:
            print(f"  - {cmd}")
        print()
    else:
        ok, message = ollama_ready()
        print(f"Preflight: {message}")
        if not ok:
            print("\nERROR: the full pipeline needs a local model. Either start "
                  "Ollama and pull gemma4:12b, or run the deterministic half:\n"
                  "  py scripts/run_demo.py --no-ai", file=sys.stderr)
            return 1
        print()

    started = time.perf_counter()
    for n, (script, argv, _, produces) in enumerate(steps, start=1):
        # Only when the user actually asked for a different folder, so a plain
        # run emits exactly the commands the README documents.
        if args.input is not None and script in TAKES_INPUT:
            argv = [*argv, "--input", str(intake)]
        label = command_line(script, argv)
        print(f"[{n}/{len(steps)}] {label}  ->  {produces}")
        step_started = time.perf_counter()
        result = subprocess.run([sys.executable, str(SCRIPTS / script), *argv])
        if result.returncode != 0:
            # Stop on the first failure. Continuing would build later artifacts
            # on top of a step that did not finish, and the report would then
            # reconcile a partial set against itself and look fine.
            print(f"\nFAILED at step {n}: {label} (exit {result.returncode}). "
                  f"Pipeline stopped; later steps did not run.", file=sys.stderr)
            return result.returncode
        print(f"      done in {time.perf_counter() - step_started:.1f}s\n")

    total = time.perf_counter() - started
    print(f"Pipeline complete: {len(steps)} steps in {total:.1f}s.")
    if not args.no_ai:
        print(f"Open the report: {(ROOT / 'qc-report.html')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
