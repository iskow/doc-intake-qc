#!/usr/bin/env python3
"""Phase 1 QC gate — verify manifest.csv is a complete, correct inventory.

Runs scan.py first (so the gate always tests fresh output), then checks:
  1. Row count == number of files on disk under mock-intake/ (counted
     independently here) AND == the answer key's file_count.
  2. Absence check: the set of paths in the manifest EXACTLY equals the set of
     files on disk — nothing missed, nothing invented.
  3. Every seeded exact-duplicate pair shares one SHA-256 hash in the manifest.
  4. Every row has a non-empty hash and a populated true_type (no blank cells).
  5. The archive is listed as a single file — its inner entries are NOT rows.
  6. Read-only: the fixture is byte-for-byte unchanged after scanning
     (compare a hash of every file before and after the scan run).

Exit code 0 = gate passed; 1 = failures (listed).
Run:  py scripts/qc_phase1.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTAKE = ROOT / "mock-intake"
MANIFEST = ROOT / "manifest.csv"

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
    """Map every fixture file -> its hash. Used to prove the scan is read-only:
    take this before and after running scan.py; the two maps must be identical."""
    return {p.relative_to(ROOT).as_posix(): sha256(p)
            for p in INTAKE.rglob("*") if p.is_file()}


def main() -> int:
    key = json.loads((ROOT / "seeded-errors.json").read_text(encoding="utf-8"))

    # Fingerprint the fixture, run the scanner, fingerprint again.
    before = fixture_fingerprint()
    subprocess.run([sys.executable, str(ROOT / "scripts" / "scan.py")], check=True)
    after = fixture_fingerprint()

    # 6. Read-only: nothing under mock-intake/ changed.
    check(before == after, "read-only: fixture byte-for-byte unchanged after scan")

    # Load the manifest the scan just wrote.
    with MANIFEST.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    manifest_paths = [r["path"] for r in rows]

    # 1. Row count reconciles with disk and with the answer key.
    disk_files = sorted(p.relative_to(ROOT).as_posix()
                        for p in INTAKE.rglob("*") if p.is_file())
    check(len(rows) == len(disk_files),
          f"row count ({len(rows)}) == files on disk ({len(disk_files)})")
    check(len(rows) == key["file_count"],
          f"row count ({len(rows)}) == answer-key file_count ({key['file_count']})")

    # 2. Absence check: manifest paths exactly equal disk paths.
    missing = set(disk_files) - set(manifest_paths)
    extra = set(manifest_paths) - set(disk_files)
    check(not missing, f"no files missed by the scan (missing: {sorted(missing) or 'none'})")
    check(not extra, f"no phantom rows in the manifest (extra: {sorted(extra) or 'none'})")

    # Duplicate rows (same path twice) would inflate the count silently.
    dupe_rows = [p for p, n in Counter(manifest_paths).items() if n > 1]
    check(not dupe_rows, f"no duplicate rows (repeats: {dupe_rows or 'none'})")

    # 3. Every seeded exact-duplicate pair shares a hash in the manifest.
    hash_by_path = {r["path"]: r["sha256"] for r in rows}
    for s in (s for s in key["seeded_errors"] if s["type"] == "exact-duplicate"):
        a, b = s["paths"]
        check(hash_by_path.get(a) and hash_by_path.get(a) == hash_by_path.get(b),
              f"exact-duplicate pair shares a hash: {a} == {b}")

    # 4. No blank hashes or true_types (every row fully populated).
    check(all(r["sha256"] for r in rows), "every row has a non-empty SHA-256")
    check(all(r["true_type"] for r in rows), "every row has a true_type")

    # 5. Archive listed as one file; its inner entries are not rows.
    check("mock-intake/old_backup.zip" in manifest_paths, "zip is listed as a file")
    check(not any("old_drive/" in p for p in manifest_paths),
          "zip contents are NOT expanded into rows")

    print(f"PASS: {len(passes)}  FAIL: {len(failures)}")
    for f in failures:
        print(f"  FAIL: {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
