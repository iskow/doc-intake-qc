#!/usr/bin/env python3
"""Phase 2 QC gate — prove the rules engine catches every seeded error.

Runs scan.py then rules.py (so the gate always tests fresh output), then checks:
  1. ABSENCE CHECK — every seeded error in the answer key appears in
     exceptions.csv under the rule that is supposed to catch it. This is the
     gate that matters: it asks "what should be here that isn't?"
  2. NO FALSE CLEAN — every file with NO seeded error has NO exception rows.
     A rule that flags everything would pass check 1; this catches that.
  3. Totals reconcile: flagged files + clean files == manifest rows == files
     on disk.
  4. Author enrichment: the manifest's author column matches the answer key's
     authors map exactly (0 mismatches), Unassigned where the key says null.
  5. Every exception row is well-formed (known rule_id, known severity, no
     blank cells) and names a real file in the manifest.
  6. Read-only: the fixture is byte-for-byte unchanged after the whole run.

Exit code 0 = gate passed; 1 = failures (listed).
Run:  py scripts/qc_phase2.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INTAKE = ROOT / "mock-intake"
MANIFEST = ROOT / "manifest.csv"
EXCEPTIONS = ROOT / "exceptions.csv"

# Which rule is responsible for catching each seeded-error type in the answer
# key. This mapping IS the contract between the fixture and the engine.
TYPE_TO_RULE = {
    "exact-duplicate":    "EXACT_DUP",
    "near-duplicate-name": "NEAR_DUP_NAME",
    "naming-violation":   "NAMING",
    "extension-mismatch": "EXT_MISMATCH",
    "zero-byte":          "ZERO_BYTE",
    "date-anomaly":       "DATE_ANOMALY",
    "junk-file":          "JUNK_FILE",
    "archive":            "ARCHIVE",
    "deep-nesting":       "DEEP_NESTING",
}

VALID_SEVERITIES = {"high", "medium", "low"}

failures: list[str] = []
passes: list[str] = []


def check(ok: bool, label: str) -> None:
    (passes if ok else failures).append(label)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def fixture_fingerprint() -> dict[str, str]:
    """Every fixture file -> its hash. Taken before and after the run to prove
    the pipeline never modified the originals."""
    return {p.relative_to(ROOT).as_posix(): sha256(p)
            for p in INTAKE.rglob("*") if p.is_file()}


def main() -> int:
    key = json.loads((ROOT / "seeded-errors.json").read_text(encoding="utf-8"))

    # Fingerprint, run the full pipeline, fingerprint again.
    before = fixture_fingerprint()
    for script in ("scan.py", "rules.py"):
        subprocess.run([sys.executable, str(ROOT / "scripts" / script)], check=True)
    after = fixture_fingerprint()

    # 6. Read-only guarantee.
    check(before == after, "read-only: fixture byte-for-byte unchanged after scan+rules")

    manifest = pd.read_csv(MANIFEST, dtype={"extension": str}, keep_default_na=False)
    exc = pd.read_csv(EXCEPTIONS, keep_default_na=False)

    # Index the exceptions as a set of (path, rule_id) pairs for fast lookup.
    flagged_pairs = set(zip(exc["path"], exc["rule_id"]))
    flagged_paths = set(exc["path"])
    manifest_paths = set(manifest["path"])

    # --- 1. ABSENCE CHECK ---------------------------------------------------
    # Every seeded error must appear under its responsible rule.
    seeded_paths: set[str] = set()
    missed: list[str] = []
    for seed in key["seeded_errors"]:
        rule = TYPE_TO_RULE.get(seed["type"])
        if rule is None:
            missed.append(f"UNMAPPED seeded type '{seed['type']}' — no rule owns it")
            continue
        for path in seed["paths"]:
            seeded_paths.add(path)
            if (path, rule) not in flagged_pairs:
                missed.append(f"{rule} missed {path}")
    check(not missed, f"every seeded error caught by its rule ({len(seeded_paths)} files)")
    for m in missed:
        failures.append(f"  -> {m}")

    # Every seeded-error TYPE is represented by at least one exception row.
    for seed_type, rule in TYPE_TO_RULE.items():
        present = any(s["type"] == seed_type for s in key["seeded_errors"])
        if present:
            check(rule in set(exc["rule_id"]), f"rule {rule} fired at least once")

    # --- 2. NO FALSE CLEAN --------------------------------------------------
    # Files carrying no seeded error must carry no exceptions either.
    clean_paths = manifest_paths - seeded_paths
    wrongly_flagged = sorted(clean_paths & flagged_paths)
    check(not wrongly_flagged,
          f"no clean file was flagged ({len(clean_paths)} clean files)")
    for w in wrongly_flagged:
        rules_hit = sorted(exc.loc[exc["path"] == w, "rule_id"])
        failures.append(f"  -> false positive on {w} ({', '.join(rules_hit)})")

    # Inverse: every seeded-error file appears somewhere in the exceptions.
    unflagged_seeded = sorted(seeded_paths - flagged_paths)
    check(not unflagged_seeded, "every seeded-error file appears in exceptions.csv")
    for u in unflagged_seeded:
        failures.append(f"  -> seeded file absent from exceptions: {u}")

    # --- 3. TOTALS RECONCILE ------------------------------------------------
    disk_count = sum(1 for p in INTAKE.rglob("*") if p.is_file())
    check(len(manifest) == disk_count == key["file_count"],
          f"manifest rows ({len(manifest)}) == disk ({disk_count}) == key ({key['file_count']})")
    check(len(flagged_paths) + len(clean_paths) == len(manifest),
          f"flagged ({len(flagged_paths)}) + clean ({len(clean_paths)}) "
          f"== manifest rows ({len(manifest)})")

    # --- 4. AUTHOR ENRICHMENT ------------------------------------------------
    author_by_path = dict(zip(manifest["path"], manifest["author"]))
    mismatches: list[str] = []
    for path, expected in key["authors"].items():
        want = expected if expected is not None else "Unassigned"
        got = author_by_path.get(path)
        if got != want:
            mismatches.append(f"{path}: expected '{want}', got '{got}'")
    check(not mismatches,
          f"author column matches the answer key ({len(key['authors'])} files, 0 mismatches)")
    for m in mismatches:
        failures.append(f"  -> author mismatch: {m}")

    check(all(str(a).strip() for a in manifest["author"]),
          "no blank author cells (Unassigned used instead)")

    # --- 5. EXCEPTION ROWS ARE WELL-FORMED -----------------------------------
    unknown_rules = sorted(set(exc["rule_id"]) - set(TYPE_TO_RULE.values()))
    check(not unknown_rules, f"all rule_ids are known (unexpected: {unknown_rules or 'none'})")

    bad_sev = sorted(set(exc["severity"]) - VALID_SEVERITIES)
    check(not bad_sev, f"all severities valid (unexpected: {bad_sev or 'none'})")

    check(all(str(d).strip() for d in exc["detail"]),
          "every exception row has a non-empty detail")

    phantom = sorted(flagged_paths - manifest_paths)
    check(not phantom, f"every exception names a real manifest file (phantom: {phantom or 'none'})")

    check(not exc.duplicated(subset=["path", "rule_id"]).any(),
          "no duplicate (path, rule_id) exception rows")

    # --- Report --------------------------------------------------------------
    print()
    print(f"Manifest: {len(manifest)} files | Exceptions: {len(exc)} rows "
          f"across {len(flagged_paths)} files | Clean: {len(clean_paths)} files")
    print()
    print(f"PASS: {len(passes)}  FAIL: {len([f for f in failures if not f.startswith('  ->')])}")
    for f in failures:
        print(f"  FAIL: {f}" if not f.startswith("  ->") else f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
