#!/usr/bin/env python3
"""Phase 4 — Non-destructive organizer for the Document Intake QC Agent.

Copies every file in the intake into a bucketed folder tree, choosing the
bucket from ONE of three independent axes:

  --by class      the Phase 3 content label (classifications.csv)
  --by author     the embedded document author (manifest.csv `author` column)
  --by custodian  the collection source (custodian-map.csv, a declared input)

Output layout:
    organized/by-<mode>/<value>/<original path under the intake>/<filename>

The original folder structure is preserved UNDER the bucket rather than
flattened into it (Phase 4b). Where a file sat in the client's environment is
itself metadata — "the contract in Old Files/2019 archive/deep/nested" is a
different fact from "a contract" — and flattening destroys it silently.

Three axes, not one, because they answer different questions and routinely
disagree. `CTR-2026-01_ServiceAgreement_AcmeSupply.docx` names Acme in the
filename, was WRITTEN by Dana Cruz of Meridian, and was COLLECTED from Legal.
A tool that collapses those into "one folder per client" quietly invents facts.
See DECISIONS 2026-07-20 for why author, party, and custodian stay separate.

The intake folder is whichever one scan.py recorded in intake-root.txt. Passing
--input is optional and is checked against that record rather than trusted: a
run that organizes folder B from a manifest built for folder A reconciles
perfectly and is completely wrong, so the mismatch stops the run (Phase 6).

NON-DESTRUCTIVE BY CONSTRUCTION — this is the whole point of the script:
  * files under the intake are only ever opened for reading
  * the script refuses to run if the output folder is inside the intake folder
  * originals are never renamed, moved, or deleted
  * a filename collision STOPS the run before a single byte is copied, rather
    than silently suffixing a name that no longer matches what was collected

METADATA FIDELITY (Phase 4b) — copies are made with `robocopy /COPY:DATSO`,
measured on this machine to preserve creation, modified and accessed times, the
NTFS ACL, the owner, and alternate data streams, with the directory timestamps
of the mirrored client folders carried by `/DCOPY:DAT`. `shutil.copy2` was
measured to lose creation time and to replace the ACL with inherited
permissions, which is why it is now only the non-Windows fallback.

Read the honest limits before quoting any of that at a client: the ORIGINAL
access time is destroyed in Phase 1, because reading a file to hash it updates
its access time. See DECISIONS 2026-07-21 (metadata fidelity, measured).

Every run also writes a cross-reference CSV — original path, new path, hash —
which is the only record of where each file sat before it was re-foldered.

Files with no value on the chosen axis go to `Unassigned/`. They are never
guessed into a bucket and never dropped — an unclassified photo, a file with no
embedded author, and a file from an unknown source all stay visible.

Run:  py scripts/organize.py --by class
      py scripts/organize.py --by custodian --dry-run
Requires: manifest.csv and intake-root.txt (scan.py). `--by class` also needs
classifications.csv (classify.py).
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
from fnmatch import fnmatchcase
from pathlib import Path

from intake import (add_input_arg, from_manifest_path, intake_for_downstream,
                    repo_relative, strip_intake_prefix)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifest.csv"
CLASSIFICATIONS = ROOT / "classifications.csv"
CUSTODIAN_MAP = ROOT / "custodian-map.csv"

MODES = ("class", "author", "custodian")

WINDOWS = os.name == "nt"

# robocopy flags, chosen by measurement rather than from memory (spiked
# 2026-07-21, see DECISIONS):
#   /COPY:D data  A attributes  T timestamps  S security (ACL)  O owner
#   /DCOPY:DAT    the same three for the directories it creates, so the
#                 mirrored client folders keep their own dates
#   /R:0 /W:0     never retry — a locked file must fail loudly and stop the
#                 run, not stall it for 30 retries x 30 seconds
#   /NJH /NJS /NP /NDL /NFL   quiet; this script does its own reporting
# NOT /COPYALL: it adds /COPY:U (auditing), which needs elevation and was
# measured to fail with exit 16 unelevated — copying nothing while a script
# that ignores exit codes reads it as success.
ROBOCOPY_FLAGS = ["/COPY:DATSO", "/DCOPY:DAT", "/R:0", "/W:0",
                  "/NJH", "/NJS", "/NP", "/NDL", "/NFL"]

# robocopy signals success with exit codes 0-7 (bit flags: 1 = files copied,
# 2 = extra files present, 4 = mismatches). 8 and above are real failures.
ROBOCOPY_FAIL = 8

# Windows caps a command line near 32k characters, and filenames are passed to
# robocopy as arguments. One folder holding thousands of files would silently
# overflow that, so batches are capped well below the limit.
ARG_BUDGET = 8000

# The bucket for "this axis has no value for this file". Shared by all three
# modes and matched exactly by scan.py's author sentinel, so an unauthored file
# lands here without any translation step.
UNASSIGNED = "Unassigned"

# Characters Windows forbids in a folder name. Bucket values come from file
# metadata and a config file, so they are usually clean — this is a guard, not
# a transformation we expect to fire.
ILLEGAL = '<>:"/\\|?*'


# --- Bucket sources: one function per axis ---------------------------------
# Each returns {manifest path -> bucket value}. Keeping them separate means a
# broken axis can never leak into another one, and the QC gate can recompute
# any single axis from its own source of truth.

def read_csv_rows(path: Path) -> list[dict[str, str]]:
    """Read a CSV into a list of dicts, leaving every value as a plain string.

    The stdlib csv module is used rather than pandas because this script only
    ever walks rows in order — there is no grouping or filtering to justify the
    dependency. (rules.py earns pandas; this doesn't. See DECISIONS 2026-07-19
    for the same reasoning applied to scan.py.)
    """
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def buckets_by_class() -> dict[str, str]:
    """Bucket = the Phase 3 content label; Unassigned when nothing was labeled.

    classify.py writes `status` = ok only when it actually read text and got a
    label back. Anything else (a photo, an empty file, the zip) carries the
    `unclassified` label, and unclassified is not a category — it is the
    absence of one, so it routes to Unassigned rather than becoming a folder
    named "unclassified" that looks like a real class.
    """
    if not CLASSIFICATIONS.exists():
        raise SystemExit(
            "classifications.csv not found — run `py scripts/classify.py` first "
            "(or use --by author / --by custodian, which don't need it)."
        )
    return {r["path"]: (r["label"] if r["status"] == "ok" else UNASSIGNED)
            for r in read_csv_rows(CLASSIFICATIONS)}


def buckets_by_author(manifest: list[dict[str, str]]) -> dict[str, str]:
    """Bucket = the embedded document author read in Phase 2.

    No translation needed: scan.py already writes the literal string
    "Unassigned" for every file whose format can't carry an author or whose
    author field is blank. The honesty rule lives there, not here.
    """
    return {r["path"]: (r["author"] or UNASSIGNED) for r in manifest}


def load_custodian_rules() -> list[tuple[str, str]]:
    """Load custodian-map.csv as an ORDERED list of (pattern, custodian).

    Order is the semantics: first match wins. That lets a specific file
    override the folder it sits in — the map's first row pulls the Financials
    workbook out of Operations and gives it to Finance, which is what actually
    happens when one document in a folder came from somewhere else.
    """
    return [(r["pattern"], r["custodian"]) for r in read_csv_rows(CUSTODIAN_MAP)]


def match_custodian(inner_path: str, rules: list[tuple[str, str]]) -> str:
    """Return the first custodian whose pattern matches this path.

    `inner_path` is the file's path INSIDE the intake — 'Invoices/INV-1.pdf',
    not 'mock-intake/Invoices/INV-1.pdf'. That is a Phase 6 change and it is
    what makes the map portable: patterns describe the client's folder
    structure, which is the thing a collection log actually knows about, and
    no longer the name of the folder we happened to stage the copy in. A map
    written against 'mock-intake/...' matched nothing on any real engagement
    and sent every file to Unassigned.

    fnmatch teaching note, two parts:
      * `fnmatchcase` is used with both sides lowercased by hand, rather than
        plain `fnmatch`, because `fnmatch` folds case using the LOCAL OS rules
        (case-insensitive on Windows, sensitive on Linux). Lowercasing
        explicitly makes the result identical on every machine, which the QC
        gate depends on. The fixture needs this: one invoice is filed as
        "inv 2026-004 brightworks FINAL.pdf" in lowercase.
      * `*` in fnmatch matches across "/" too, so `Invoices/*` also covers
        anything nested under Invoices. That is what we want for a collection
        source — a custodian's dump includes its subfolders.
    """
    low = inner_path.lower()
    for pattern, custodian in rules:
        if fnmatchcase(low, pattern.lower()):
            return custodian
    return UNASSIGNED


def buckets_by_custodian(manifest: list[dict[str, str]]) -> dict[str, str]:
    """Bucket = collection source, from the declared mapping table.

    Worth being precise about what this is: custodian is NOT derived from the
    filename's party token (DECISIONS 2026-07-20 rejects that outright — it
    conflates three different axes and over-claims). It comes from an explicit
    human-authored table standing in for the collection log a client provides.
    A declared input can be wrong, but it is never an invented fact, and
    anything the table doesn't cover stays Unassigned instead of being guessed.
    """
    rules = load_custodian_rules()
    return {r["path"]: match_custodian(strip_intake_prefix(r["path"]), rules)
            for r in manifest}


# --- Planning: every destination is decided before anything is written -----

def safe_folder(value: str) -> str:
    """Turn a bucket value into a folder name that Windows will accept."""
    cleaned = "".join("_" if c in ILLEGAL or ord(c) < 32 else c for c in value)
    # Windows silently strips trailing dots and spaces from folder names, which
    # would make two different buckets collide. Strip them ourselves so the
    # name we log is the name on disk.
    cleaned = cleaned.rstrip(". ").strip()
    return cleaned or UNASSIGNED


def plan_copies(manifest: list[dict[str, str]], buckets: dict[str, str],
                out_root: Path, intake: Path
                ) -> list[tuple[Path, Path, str, dict[str, str]]]:
    """Build the complete copy plan as [(src, dest, bucket, manifest row)].

    Every destination is computed before anything is written, so the collision
    check can inspect the whole plan rather than discovering a clash halfway
    through a tree it has already started building.

    The destination keeps the file's path RELATIVE TO THE INTAKE ROOT, nested
    under its bucket:  Reports/RPT-Q1.pdf  ->  <bucket>/Reports/RPT-Q1.pdf
    """
    plan = []
    for row in manifest:
        bucket = safe_folder(buckets.get(row["path"], UNASSIGNED))
        relpath = strip_intake_prefix(row["path"])
        plan.append((from_manifest_path(row["path"], intake),
                     out_root / bucket / relpath, bucket, row))
    return plan


def check_collisions(plan: list[tuple[Path, Path, str, dict[str, str]]]) -> None:
    """Refuse to run if two files would be written to the same destination.

    Joel's call, and it is the defensible one: a file whose processed name no
    longer matches what was collected is worse than a stopped run. The previous
    behaviour auto-suffixed `__2`, which produced a filename the client never
    had and which nothing else in the deliverable refers to.

    This is a PREFLIGHT. The whole plan is checked before the first byte moves,
    so the operator gets the complete list of what to fix in one pass instead
    of a half-built tree and a single error.

    Preserving each file's original relative path (Phase 4b) makes a collision
    structurally impossible: two files collide only if they share a relative
    path AND a bucket, and a relative path is unique on a filesystem. So this
    never fires on real input. It stays anyway, because "cannot happen" and
    "does not happen" are different claims, and only the second survives
    somebody editing the destination rule. The QC gate proves it can fire.
    """
    seen: dict[Path, str] = {}
    clashes: list[str] = []
    for _, dest, _, row in plan:
        first = seen.get(dest)
        if first is None:
            seen[dest] = row["path"]
        else:
            clashes.append(f"    {dest}\n        <- {first}\n        <- {row['path']}")
    if clashes:
        raise SystemExit(
            f"COLLISION: {len(clashes)} destination path(s) would be written twice.\n"
            "Nothing has been copied. Renaming one of the sources would break the "
            "link back to what was collected, so this is yours to resolve — fix the "
            "source names or the bucket rule, then rerun.\n" + "\n".join(clashes))


# --- Copying ---------------------------------------------------------------

def batch_by_folder(plan: list[tuple[Path, Path, str, dict[str, str]]]
                    ) -> dict[tuple[Path, Path], list[str]]:
    """Group the plan into one robocopy call per (source folder, dest folder).

    robocopy copies folder-to-folder with a filename filter, so there is no
    need for a subprocess per file — every file sharing a source folder AND a
    destination folder rides in one call. That was the standing objection to
    using robocopy at all (DECISIONS 2026-07-21), and this is what answers it:
    the call count scales with the number of folders, not the number of files.
    """
    groups: dict[tuple[Path, Path], list[str]] = {}
    for src, dest, _, _ in plan:
        groups.setdefault((src.parent, dest.parent), []).append(src.name)
    return groups


def arg_batches(names: list[str]) -> list[list[str]]:
    """Split filenames into batches that fit inside one Windows command line."""
    batches, batch, size = [], [], 0
    for name in names:
        if batch and size + len(name) + 3 > ARG_BUDGET:
            batches.append(batch)
            batch, size = [], 0
        batch.append(name)
        size += len(name) + 3          # +3 covers the quotes and the space
    if batch:
        batches.append(batch)
    return batches


def robocopy_files(src_dir: Path, dest_dir: Path, names: list[str]) -> None:
    """Copy the named files from src_dir to dest_dir, metadata intact.

    Passing each filename as its own argument makes robocopy treat it as a
    filter. Those filters are wildcard patterns, which sounds dangerous until
    you notice `*` and `?` are illegal in Windows filenames — so a real name
    can never be misread as a pattern. Spaces, parentheses and the `~$` Office
    lock prefix were all spiked and pass through cleanly.
    """
    for batch in arg_batches(names):
        result = subprocess.run(
            ["robocopy", str(src_dir), str(dest_dir), *batch, *ROBOCOPY_FLAGS],
            capture_output=True, text=True)
        # Exit codes are checked because the failure mode is silent: robocopy
        # returns a status and carries on, so an unchecked call can copy
        # nothing at all and still look like it worked.
        if result.returncode >= ROBOCOPY_FAIL:
            raise SystemExit(
                f"robocopy failed (exit {result.returncode}) copying "
                f"{len(batch)} file(s)\n  from {src_dir}\n  to   {dest_dir}\n"
                f"{result.stdout}{result.stderr}")


def copy_fallback(plan: list[tuple[Path, Path, str, dict[str, str]]]) -> None:
    """Non-Windows path: copy2, which preserves modified times and nothing else.

    Degrading rather than crashing, but loudly — a caller who gets this and
    believes they have a metadata-faithful copy has been misled, so it says so.
    """
    for src, dest, _, _ in plan:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    print("WARNING: not Windows — copied with shutil.copy2. Creation times, "
          "ACLs and owner are NOT preserved. Do not describe this output as "
          "metadata-faithful.")


def crossref_path(out_root: Path) -> Path:
    """Where the cross-reference lands: beside the tree, never inside it.

    Inside would mean the organized tree contains a file that is not a copy of
    anything, which quietly breaks the "every file here traces to an original"
    property the QC gate checks and a client reviewer relies on.
    """
    return out_root.parent / f"crossref-{out_root.name}.csv"


def write_crossref(path: Path, plan: list[tuple[Path, Path, str, dict[str, str]]]) -> None:
    """Record original path -> new path -> hash for every file copied.

    Mandatory from Phase 4b on. In 4a the mapping was the identity — files kept
    their names and their folder, so a table recording that would have been
    ceremony. Re-foldering breaks that: once a file has moved, this CSV is the
    only surviving record of where it sat in the client's environment.

    The hash is the scan-time hash of the ORIGINAL, taken from manifest.csv
    rather than recomputed here, so the column is a provenance record from
    before any copy existed rather than a restatement of what we just wrote.
    """
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["original_path", "original_folder", "bucket",
                         "new_path", "sha256", "size_bytes", "created", "modified"])
        for src, dest, bucket, row in plan:
            original_folder = Path(strip_intake_prefix(row["path"])).parent
            writer.writerow([
                row["path"],
                original_folder.as_posix() if original_folder.parts else "",
                bucket,
                repo_relative(dest),
                row["sha256"],
                row["size_bytes"],
                row["created"],
                row["modified"],
            ])


def organize(mode: str, out_root: Path, intake: Path,
             dry_run: bool = False) -> dict[str, int]:
    """Copy every manifest file into out_root/<bucket>/. Returns bucket counts."""
    manifest = read_csv_rows(MANIFEST)

    if mode == "class":
        buckets = buckets_by_class()
    elif mode == "author":
        buckets = buckets_by_author(manifest)
    else:
        buckets = buckets_by_custodian(manifest)

    # Safety guard. Writing inside the intake folder would break the one
    # promise this whole project makes, so it is checked before a single byte
    # moves rather than trusted to the caller. resolve() first so a path like
    # "organized/../mock-intake/x" can't sneak past a string comparison.
    resolved = out_root.resolve()
    if resolved == intake or intake in resolved.parents:
        raise SystemExit(f"refusing to write inside the intake folder: {resolved}")

    plan = plan_copies(manifest, buckets, out_root, intake)
    # Runs on --dry-run too: catching a collision is exactly what a dry run is
    # for, and it costs nothing.
    check_collisions(plan)

    if not dry_run:
        # Start clean so a rerun can't leave files from a previous run behind
        # and inflate the count. Only ever removes this mode's own output.
        if out_root.exists():
            shutil.rmtree(out_root)
        out_root.mkdir(parents=True)

        if WINDOWS:
            for (src_dir, dest_dir), names in batch_by_folder(plan).items():
                robocopy_files(src_dir, dest_dir, names)
        else:
            copy_fallback(plan)

        write_crossref(crossref_path(out_root), plan)

    counts: dict[str, int] = {}
    for _, _, bucket, _ in plan:
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Non-destructively copy the intake into bucketed folders.")
    parser.add_argument("--by", required=True, choices=MODES, dest="mode",
                        help="which axis to bucket on")
    parser.add_argument("--out", default=None, type=Path,
                        help="output root (default: organized/by-<mode>)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the buckets without copying anything")
    add_input_arg(parser)
    args = parser.parse_args()

    # Defaults to the folder scan.py recorded; --input is verified against it,
    # never trusted over it.
    intake = intake_for_downstream(args.input)

    # Each mode gets its own root so running all three doesn't merge three
    # different answers into one folder tree.
    out_root = args.out or (ROOT / "organized" / f"by-{args.mode}")
    counts = organize(args.mode, out_root, intake, args.dry_run)

    total = sum(counts.values())
    verb = "Would organize" if args.dry_run else "Organized"
    print(f"{verb} {total} files by {args.mode} -> {repo_relative(out_root)}/")
    for folder in sorted(counts, key=lambda k: (k == UNASSIGNED, k.lower())):
        print(f"  {folder:<36} {counts[folder]}")
    if not args.dry_run:
        print(f"Cross-reference: {repo_relative(crossref_path(out_root))}")
        if not WINDOWS:
            print("Metadata: modified times only (non-Windows fallback).")


if __name__ == "__main__":
    main()
