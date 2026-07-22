#!/usr/bin/env python3
"""Phase 6 — Where the intake folder lives, and how every script agrees on it.

Until Phase 6 the intake was a constant: `INTAKE = ROOT / "mock-intake"`,
repeated in scan.py, organize.py and run_demo.py. That made the tool
undeployable — a client's set had to be staged at exactly that path — and it
made the three copies of the constant a hazard in their own right, because
editing one and not the others produces a run that reads one folder and
organizes another with no error at all.

This module is the single answer to both. It owns the flag, the path scheme,
and the agreement between scripts.

THE PATH SCHEME (the load-bearing decision)
-------------------------------------------
manifest.csv's `path` column is stored relative to the intake's PARENT, not
relative to the repo root:

    intake C:\\repo\\mock-intake   + Invoices/INV-1.pdf -> mock-intake/Invoices/INV-1.pdf
    intake D:\\ClientData\\Acme    + Invoices/INV-1.pdf -> Acme/Invoices/INV-1.pdf

Two properties make this the right scheme rather than merely a working one:

  * For the shipped fixture it is BYTE-IDENTICAL to the old
    `relative_to(ROOT)` output, because the fixture's parent IS the repo root.
    So seeded-errors.json, custodian-map.csv and all six QC gates keep passing
    against paths they already knew. Measured before writing this — the old
    scheme raises ValueError the moment the intake sits off the repo root.
  * It keeps the leading folder name, which downstream code relies on: the
    bucket layout strips it back off with `relative_to(INTAKE.name)`, and a
    reader can see at a glance which collection a row came from.

THE AGREEMENT (why a record file exists)
-----------------------------------------
scan.py writes the resolved intake root to `intake-root.txt`. organize.py
reads it and refuses to run if its own --input disagrees. Adding a flag
without this would not have removed the old hazard, only made it easier to
trigger — the same silent mismatch, now one typo away instead of three edits
away. A mismatch is loud and stops the run.

The record is a generated artifact, like manifest.csv: gitignored, rewritten
by every scan, never hand-edited.
"""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The shipped fixture. Still the default, so every documented command in the
# README keeps working with no flag — Phase 6 adds a capability, it does not
# move anyone's cheese.
DEFAULT_INTAKE = ROOT / "mock-intake"

# Written by scan.py, read by organize.py. Absolute, one line, no ceremony.
INTAKE_RECORD = ROOT / "intake-root.txt"

INPUT_HELP = ("folder to scan (default: mock-intake/, the shipped demo "
              "fixture). Point this at the client's staged copy — never at "
              "their only copy.")


def add_input_arg(parser: argparse.ArgumentParser) -> None:
    """Add --input to a parser. One definition, so the three scripts cannot
    drift into describing the same flag three different ways."""
    parser.add_argument("--input", default=None, type=Path, dest="input",
                        metavar="FOLDER", help=INPUT_HELP)


def resolve_intake(value: Path | None) -> Path:
    """Turn a --input value (or None) into a validated absolute intake root.

    resolve() is called before anything else so that a relative path, a path
    with `..` in it, and a symlink all reduce to one canonical form. Everything
    downstream — the safety guard that refuses to write inside the intake, the
    mismatch check, the manifest paths — compares resolved paths, and comparing
    unresolved ones is how two names for the same folder slip past a check.
    """
    intake = (value or DEFAULT_INTAKE).resolve()
    if not intake.is_dir():
        raise SystemExit(
            f"ERROR: intake folder not found: {intake}\n"
            f"       Pass an existing folder with --input, or generate the "
            f"demo fixture with: py scripts/make_mock_data.py")
    if intake.parent == intake:
        # A drive root (D:\) has no parent to be relative to, so the path
        # scheme has nothing to hang the collection name on. Refusing is
        # better than emitting paths that cannot be read back.
        raise SystemExit(
            f"ERROR: the intake cannot be a drive root: {intake}\n"
            f"       Put the collection in a named folder (D:\\ClientData\\Acme) "
            f"so its name can identify it in the manifest and the report.")
    return intake


def record_intake(intake: Path) -> None:
    """Write the resolved intake root next to the manifest that describes it."""
    INTAKE_RECORD.write_text(f"{intake}\n", encoding="utf-8")


def read_record() -> Path | None:
    """The intake root the current manifest was built from, or None."""
    if not INTAKE_RECORD.is_file():
        return None
    recorded = INTAKE_RECORD.read_text(encoding="utf-8").strip()
    # resolve() so the comparison in intake_for_downstream and the safety guard
    # in organize.py are both against the same canonical form the writer used.
    return Path(recorded).resolve() if recorded else None


def intake_for_downstream(value: Path | None) -> Path:
    """The intake root for a script that CONSUMES manifest.csv.

    This is the guard. Three cases, and only the first is a judgment call:

      * --input given and it disagrees with what scan.py recorded -> stop.
        This is the whole point of the record. Organizing folder B using a
        manifest built from folder A produces a complete, plausible, wrong
        result: every count reconciles, because they all reconcile against
        the wrong source.
      * --input given and it agrees -> use it.
      * --input omitted -> use the record, so the common case needs no flag
        and cannot be got wrong by forgetting one.
    """
    recorded = read_record()
    if recorded is None:
        raise SystemExit(
            f"ERROR: {INTAKE_RECORD.name} not found — run the scanner first:\n"
            f"         py scripts/scan.py [--input FOLDER]\n"
            f"       It records which folder the manifest describes, so later "
            f"steps cannot silently work on a different one.")
    if value is None:
        return recorded
    requested = resolve_intake(value)
    if requested != recorded:
        raise SystemExit(
            f"ERROR: --input does not match the folder this manifest describes.\n"
            f"         manifest was built from: {recorded}\n"
            f"         --input asks for:        {requested}\n"
            f"       Nothing has run. Rescan the folder you want, or drop "
            f"--input to use the one already scanned:\n"
            f"         py scripts/scan.py --input {requested}")
    return requested


def to_manifest_path(file: Path, intake: Path) -> str:
    """The manifest `path` value for a file: intake folder name + its path
    inside the intake, POSIX-separated so the CSV reads the same on any OS."""
    return file.relative_to(intake.parent).as_posix()


def from_manifest_path(rel: str, intake: Path) -> Path:
    """The inverse of to_manifest_path — back to the real file on disk.

    The exact inverse matters more than it looks: organize.py copies FROM this
    path, so an approximation here would copy the wrong bytes while every
    count still reconciled.
    """
    return intake.parent / rel


def repo_relative(path: Path) -> str:
    """A readable form of `path`: relative to the repo when it sits inside it,
    absolute when it does not.

    Found by testing rather than by reading (Phase 6): the cross-reference and
    the console summary both called `.relative_to(ROOT)` outright, which is
    fine while everything lives in the repo and raises ValueError the moment
    --out points at a client drive. Same assumption the intake path itself
    used to make, in a place nobody had looked.
    """
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def strip_intake_prefix(rel: str) -> str:
    """Drop the leading intake folder name: 'Acme/Invoices/x.pdf' -> 'Invoices/x.pdf'.

    Used wherever something needs to be true of a file's position INSIDE the
    collection regardless of what the collection is called — custodian
    patterns, and the folder column in the report. Both previously matched the
    literal string "mock-intake", which is why both were wrong for any other
    intake, and why a `.replace()` was a latent bug even for this one: it would
    have edited that substring anywhere in the path, not just at the front.
    """
    _, _, rest = rel.partition("/")
    return rest
