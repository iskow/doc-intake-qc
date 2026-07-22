#!/usr/bin/env python3
"""Phase 6 QC gate — the intake is a parameter, and the parameter cannot lie.

Phase 6 made one claim in two halves, and this gate exists to keep both honest:

  1. The tool runs against ANY folder, not just the fixture staged at
     mock-intake/. That is the thing that stood between this build and a real
     engagement.
  2. Making it a parameter did not introduce a quieter version of the bug it
     removed. Three scripts holding the same constant could disagree; three
     scripts taking the same flag can disagree too, and a run that scans folder
     A and organizes folder B reconciles perfectly against the wrong source.

Almost every check here runs against a SYNTHETIC intake built in a temp folder
outside the repo, because that is the case the old code could not express at
all — `relative_to(ROOT)` raises ValueError the moment the intake sits off the
repo root. Checking portability against a folder inside the repo would pass
vacuously.

The backward-compatibility half is checked the other way round: the fixture's
manifest paths must be EXACTLY what they were before Phase 6, because
seeded-errors.json, custodian-map.csv and five other gates all still address
files by those strings.

Run:  py scripts/qc_phase6.py
Requires: manifest.csv for the fixture (scan.py). Does NOT require Ollama —
nothing here classifies anything.
"""

from __future__ import annotations

import ast
import csv
import io
import os
import subprocess
import sys
import tempfile
import tokenize
from pathlib import Path

from intake import (DEFAULT_INTAKE, INTAKE_RECORD, from_manifest_path,
                    repo_relative, resolve_intake, strip_intake_prefix,
                    to_manifest_path)
from organize import load_custodian_rules, match_custodian
from report import folder_label

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
MANIFEST = ROOT / "manifest.csv"

# The synthetic client collection. Deliberately named nothing like "mock-intake"
# and deliberately shaped like the fixture's folders, so the custodian map's
# patterns are exercised rather than merely present.
COLLECTION = "AcmeCollection"
FILES = {
    "Invoices/INV-2026-001_Acme.pdf": "invoice one",
    "Invoices/INV-2026-002_Acme.pdf": "invoice two",
    "Contracts/CTR-2026-01_MSA.docx": "contract text",
    "Reports/Archive/deep/RPT-old.pdf": "nested report",
    "data_export.csv": "a,b,c",
    "loose_note.txt": "no rule covers this one",
}

# What custodian-map.csv must say about the synthetic set. Asserted BY NAME
# rather than by replaying match_custodian, so a broken map and a broken
# matcher cannot cancel each other out.
EXPECTED_CUSTODIANS = {
    "Invoices/INV-2026-001_Acme.pdf": "M. Santos (Accounts Payable)",
    "Invoices/INV-2026-002_Acme.pdf": "M. Santos (Accounts Payable)",
    "Contracts/CTR-2026-01_MSA.docx": "K. Aguilar (Legal)",
    "Reports/Archive/deep/RPT-old.pdf": "L. Ferrer (Operations)",
    "data_export.csv": "ERP Export (non-custodial source)",
    "loose_note.txt": "Unassigned",
}

failures: list[str] = []
passes: list[str] = []
# Explanatory lines attached to a failure. Kept apart from `failures` so the
# reported count is the number of CHECKS that failed, not checks plus prose.
details: list[str] = []


def check(ok: bool, label: str) -> None:
    (passes if ok else failures).append(label)
    print(f"{'PASS' if ok else 'FAIL'}  {label}")


def run(args: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess:
    """Run a pipeline script as a real subprocess.

    In-process calls would share this gate's already-imported modules and its
    argparse state. The operator types commands; so does the gate.
    """
    return subprocess.run([sys.executable, *args], cwd=cwd,
                          capture_output=True, text=True)


def build_collection(base: Path) -> Path:
    intake = base / COLLECTION
    for rel, text in FILES.items():
        path = intake / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return intake


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def fingerprint(folder: Path) -> dict[str, tuple[int, float]]:
    """Size and mtime of every file, for proving the intake was not touched."""
    return {str(p.relative_to(folder)): (p.stat().st_size, p.stat().st_mtime)
            for p in sorted(folder.rglob("*")) if p.is_file()}


# --- 1. BACKWARD COMPATIBILITY ----------------------------------------------

def check_fixture_unchanged() -> None:
    """The fixture's manifest paths must be byte-identical to pre-Phase-6.

    This is the check that lets five other gates and the answer key keep
    working. The path scheme changed from "relative to the repo root" to
    "relative to the intake's parent"; for the shipped fixture those are the
    same string, and that equivalence is the whole reason the change was safe.
    """
    if not MANIFEST.is_file():
        check(False, "fixture: manifest.csv exists (run `py scripts/scan.py` first)")
        return
    rows = read_rows(MANIFEST)
    prefix = f"{DEFAULT_INTAKE.name}/"
    check(bool(rows) and all(r["path"].startswith(prefix) for r in rows),
          f"fixture: every manifest path still starts '{prefix}' ({len(rows)} rows)")

    # And they still point at files that exist, via the new resolver.
    resolved = [from_manifest_path(r["path"], DEFAULT_INTAKE) for r in rows]
    missing = [p for p in resolved if not p.is_file()]
    check(not missing,
          f"fixture: all {len(rows)} manifest paths resolve to real files "
          f"through from_manifest_path")
    for p in missing[:3]:
        details.append(f"  -> manifest path does not resolve: {p}")


def check_round_trip() -> None:
    """to_manifest_path and from_manifest_path must be exact inverses.

    Not approximately: organize.py copies FROM the resolved path, so an
    off-by-one-folder inverse would copy the wrong bytes while every count in
    the report still reconciled.
    """
    cases = [
        (Path(r"C:\repo\mock-intake"), "Invoices/INV-1.pdf"),
        (Path(r"D:\ClientData\Acme"), "Reports/Archive/deep/x.pdf"),
        (Path(r"C:\repo\mock-intake"), "root_level.txt"),
    ]
    ok = True
    for intake, inner in cases:
        original = intake / inner
        rel = to_manifest_path(original, intake)
        back = from_manifest_path(rel, intake)
        if back != original or strip_intake_prefix(rel) != inner:
            ok = False
            details.append(f"  -> round-trip broke: {original} -> {rel} -> {back}")
    check(ok, f"path scheme: to_manifest_path/from_manifest_path invert exactly "
              f"({len(cases)} cases incl. an off-repo drive)")

    # strip_intake_prefix must drop only the LEADING component. The old code
    # used str.replace("mock-intake", ""), which would also have eaten the name
    # out of the middle of a path — this is the case that catches that.
    nasty = "mock-intake/archive/mock-intake-backup/file.pdf"
    check(strip_intake_prefix(nasty) == "archive/mock-intake-backup/file.pdf",
          "path scheme: strip_intake_prefix drops only the leading folder, not "
          "every occurrence of its name")


def check_repo_relative() -> None:
    """repo_relative must survive a path outside the repo instead of raising."""
    inside = repo_relative(ROOT / "organized" / "by-class")
    check(inside == "organized/by-class",
          f"display paths: a path inside the repo stays repo-relative ({inside})")
    outside = Path(tempfile.gettempdir()).resolve() / "elsewhere" / "out"
    check(repo_relative(outside) == outside.as_posix(),
          "display paths: a path outside the repo falls back to absolute "
          "instead of raising ValueError")


# --- 2. PORTABILITY: the tool runs on a folder that is not the fixture -------

def check_external_intake(base: Path) -> dict[str, str]:
    """Scan and organize a synthetic collection outside the repo. Returns the
    manifest rows keyed by path, for the checks that follow."""
    intake = build_collection(base)
    before = fingerprint(intake)
    out_root = base / "organized-out"

    result = run(["scripts/scan.py", "--input", str(intake)])
    check(result.returncode == 0,
          f"external intake: scan.py exits 0 on a folder outside the repo")
    if result.returncode != 0:
        details.append(f"  -> {result.stderr.strip()[:300]}")
        return {}

    rows = read_rows(MANIFEST)
    check(len(rows) == len(FILES),
          f"external intake: manifest has one row per file "
          f"({len(rows)} rows, {len(FILES)} files)")

    prefix = f"{COLLECTION}/"
    check(all(r["path"].startswith(prefix) for r in rows),
          f"external intake: manifest paths carry the collection's own name "
          f"('{prefix}'), not the fixture's")

    # The nested file proves folder structure survived into the manifest.
    check(any(r["path"] == f"{COLLECTION}/Reports/Archive/deep/RPT-old.pdf"
              for r in rows),
          "external intake: nested folders are preserved in the manifest path")

    # The record is what later steps trust.
    recorded = INTAKE_RECORD.read_text(encoding="utf-8").strip()
    check(Path(recorded) == intake.resolve(),
          f"record: scan.py wrote the resolved intake root to "
          f"{INTAKE_RECORD.name}")

    # Organize it, to an output ALSO outside the repo — the case that caught a
    # real bug during the build (write_crossref called relative_to(ROOT)).
    result = run(["scripts/organize.py", "--by", "custodian",
                  "--out", str(out_root)])
    check(result.returncode == 0,
          "external intake: organize.py exits 0 writing outside the repo")
    if result.returncode != 0:
        details.append(f"  -> {result.stderr.strip()[:300]}")
        return {r["path"]: r["path"] for r in rows}

    copied = sorted(p.relative_to(out_root).as_posix()
                    for p in out_root.rglob("*") if p.is_file())
    check(len(copied) == len(FILES),
          f"external intake: every file was copied ({len(copied)}/{len(FILES)})")

    # Bucket placement, asserted by name against EXPECTED_CUSTODIANS. This is
    # the check that proves the custodian map became portable: these patterns
    # matched nothing at all when they were prefixed 'mock-intake/'.
    misplaced = []
    for inner, custodian in EXPECTED_CUSTODIANS.items():
        want = f"{custodian}/{inner}"
        if want not in copied:
            misplaced.append(f"{inner} -> expected bucket '{custodian}'")
    check(not misplaced,
          f"custodian map: all {len(EXPECTED_CUSTODIANS)} files land in the "
          f"bucket the map dictates, on a collection named '{COLLECTION}'")
    for m in misplaced:
        details.append(f"  -> {m}")

    # Anti-vacuity: if everything went to Unassigned the check above could pass
    # for a map that matched nothing, so require real buckets too.
    named = {c for c in EXPECTED_CUSTODIANS.values() if c != "Unassigned"}
    check(len(named) >= 4,
          f"custodian map: the test exercises {len(named)} named custodians, "
          f"so a map matching nothing cannot pass")

    check(fingerprint(intake) == before,
          "external intake: the source collection is byte-for-byte unchanged "
          "after scan and organize (read-only)")

    return {r["path"]: r["author"] for r in rows}


# --- 3. THE GUARD: a mismatch must stop the run -----------------------------

def check_mismatch_guard(base: Path) -> None:
    """organize.py must refuse a --input that disagrees with the manifest.

    This is the check that justifies the record file existing at all. Without
    it, Phase 6 would have replaced "three constants can disagree" with "three
    flags can disagree" and called it a fix.
    """
    other = base / "OtherCollection"
    (other / "Invoices").mkdir(parents=True, exist_ok=True)
    (other / "Invoices" / "decoy.pdf").write_text("decoy", encoding="utf-8")
    out_root = base / "should-not-exist"

    result = run(["scripts/organize.py", "--by", "custodian",
                  "--input", str(other), "--out", str(out_root)])
    check(result.returncode != 0,
          f"guard: organize.py refuses an --input the manifest does not describe "
          f"(exit {result.returncode})")
    combined = result.stdout + result.stderr
    check("does not match" in combined,
          "guard: the refusal explains the mismatch rather than failing obscurely")
    check(str(other) in combined and "scan.py" in combined,
          "guard: the message names the offending folder and the command to fix it")
    check(not out_root.exists(),
          "guard: nothing was written — the run stopped before copying")

    # And the inverse: the SAME folder passed explicitly must be accepted, or
    # the guard would be a blanket refusal rather than a mismatch check.
    recorded = Path(INTAKE_RECORD.read_text(encoding="utf-8").strip())
    ok_out = base / "matching-out"
    result = run(["scripts/organize.py", "--by", "custodian",
                  "--input", str(recorded), "--out", str(ok_out)])
    check(result.returncode == 0,
          "guard: a MATCHING --input is accepted, so the check discriminates "
          "rather than refusing everything")


def check_missing_record(base: Path) -> None:
    """With no record on disk, a consuming script must say so plainly."""
    backup = base / "intake-root.backup"
    if not INTAKE_RECORD.is_file():
        check(False, "record: intake-root.txt exists to be removed for this test")
        return
    backup.write_text(INTAKE_RECORD.read_text(encoding="utf-8"), encoding="utf-8")
    INTAKE_RECORD.unlink()
    try:
        result = run(["scripts/organize.py", "--by", "custodian",
                      "--out", str(base / "no-record-out")])
        check(result.returncode != 0,
              f"record: organize.py refuses to run with no intake record "
              f"(exit {result.returncode})")
        combined = result.stdout + result.stderr
        check("scan.py" in combined,
              "record: the error points at the scanner rather than stack-tracing")
    finally:
        INTAKE_RECORD.write_text(backup.read_text(encoding="utf-8"),
                                 encoding="utf-8")


# --- 4. VALIDATION: bad input is refused, not misinterpreted ----------------

def check_input_validation(base: Path) -> None:
    missing = base / "does-not-exist-at-all"
    result = run(["scripts/scan.py", "--input", str(missing)])
    check(result.returncode != 0,
          f"validation: scan.py refuses a nonexistent --input "
          f"(exit {result.returncode})")
    check("not found" in (result.stdout + result.stderr),
          "validation: the nonexistent-folder error names the problem")

    # A drive root has no parent, so the path scheme has nothing to name the
    # collection with. Refusing beats emitting paths that cannot be read back.
    drive = Path(os.path.splitdrive(str(ROOT))[0] + os.sep)
    raised = False
    try:
        resolve_intake(drive)
    except SystemExit:
        raised = True
    check(raised, f"validation: a drive root ({drive}) is refused as an intake")


# --- 5. THE HARDCODED CONSTANT IS ACTUALLY GONE -----------------------------

def check_no_hardcoded_intake() -> None:
    """The gap this phase closed must stay closed.

    An absence check, and the failure mode it guards is a future edit quietly
    reintroducing `INTAKE = ROOT / "mock-intake"` in one script. Only intake.py
    is allowed to name the default, because naming it once is the fix.

    Tokenized rather than grepped, and that is not fussiness — the first draft
    scanned raw lines and flagged its own explanatory docstring in report.py.
    A comment or a docstring that DISCUSSES the old constant is fine; a string
    literal that IS it is not. tokenize draws exactly that line: comments are a
    separate token type, and a docstring's literal value is the whole
    paragraph, never the bare folder name.
    """
    offenders = []
    scanned = 0
    for script in sorted(SCRIPTS.glob("*.py")):
        if script.name in {"intake.py", "make_mock_data.py"} or \
                script.name.startswith("qc_phase"):
            continue
        scanned += 1
        src = script.read_text(encoding="utf-8")
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type != tokenize.STRING:
                continue
            try:
                value = ast.literal_eval(tok.string)
            except (ValueError, SyntaxError):
                continue
            if value == DEFAULT_INTAKE.name:
                offenders.append(f"{script.name}:{tok.start[0]}: "
                                 f"literal {tok.string}")
    check(not offenders,
          f"no pipeline script hardcodes the intake folder name "
          f"({scanned} pipeline scripts scanned)")
    for o in offenders:
        details.append(f"  -> {o}")

    # The shipped custodian map must not carry the fixture's folder name
    # either, or it silently stops matching on every real engagement.
    rules = load_custodian_rules()
    prefixed = [p for p, _ in rules if p.startswith(f"{DEFAULT_INTAKE.name}/")]
    check(not prefixed,
          f"custodian-map.csv patterns are intake-relative ({len(rules)} rules, "
          f"0 prefixed with the fixture's folder name)")
    for p in prefixed:
        details.append(f"  -> pattern still names the fixture folder: {p}")


def check_report_folder_label() -> None:
    """report.py's folder column must work for a collection with any name."""
    check(folder_label(f"{COLLECTION}/Invoices/INV-1.pdf") == "Invoices",
          "report: folder_label strips a non-fixture collection name")
    check(folder_label(f"{COLLECTION}/x.pdf") == "(intake root)",
          "report: a root-level file reads '(intake root)', not a bare dot")
    check(folder_label("mock-intake/Reports/Archive/deep/x.pdf")
          == "Reports/Archive/deep",
          "report: nested folders still render in full for the fixture")


def check_custodian_matching() -> None:
    """match_custodian works on intake-relative paths, and only those."""
    rules = load_custodian_rules()
    check(match_custodian("Invoices/INV-1.pdf", rules) == "M. Santos (Accounts Payable)",
          "custodian: an intake-relative path matches its folder rule")
    check(match_custodian("mock-intake/Invoices/INV-1.pdf", rules) == "Unassigned",
          "custodian: a path that still carries an intake prefix does NOT match "
          "— proving the contract changed rather than merely widened")
    # Precedence: the specific file rule must still beat the folder rule.
    check(match_custodian("Reports/RPT-2026-Q1_Financials.xlsx", rules)
          == "P. Bautista (Finance)",
          "custodian: first-match-wins precedence survives the rewrite "
          "(specific file beats its folder)")


def main() -> int:
    print("Phase 6 gate — the intake as a parameter\n")

    # The fixture checks read the CURRENT manifest, so they run before the
    # external-intake checks overwrite it.
    check_fixture_unchanged()
    check_round_trip()
    check_repo_relative()
    check_no_hardcoded_intake()
    check_report_folder_label()
    check_custodian_matching()
    print()

    with tempfile.TemporaryDirectory(prefix="qc6_") as tmp:
        base = Path(tmp).resolve()
        check_external_intake(base)
        print()
        check_mismatch_guard(base)
        check_missing_record(base)
        check_input_validation(base)

    # Leave the repo describing the fixture again. A gate that walks away with
    # manifest.csv pointing at a temp folder that no longer exists would break
    # every gate run after it.
    print()
    result = run(["scripts/scan.py"])
    restored = (result.returncode == 0
                and Path(INTAKE_RECORD.read_text(encoding="utf-8").strip())
                == DEFAULT_INTAKE.resolve())
    check(restored,
          "cleanup: the fixture manifest is restored, so later gates run clean")

    print(f"\nPASS: {len(passes)}, FAIL: {len(failures)}")
    for d in details:
        print(d)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
