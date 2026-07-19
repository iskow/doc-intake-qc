#!/usr/bin/env python3
"""Phase 0 QC gate — verify the fixture matches the answer key, in code.

Reads seeded-errors.json and checks every claim against the actual files:
  1. Every seeded path exists.
  2. File count on disk equals the recorded count.
  3. Zero-byte files are exactly 0 bytes.
  4. Exact-duplicate pairs are SHA-256-identical — and there are NO
     unintended hash duplicates (absence check).
  5. Extension-mismatch files really mismatch (checked via magic bytes —
     the first bytes of a file identify its true type: PDFs start with
     b'%PDF', PNGs with b'\\x89PNG').
  6. Date anomalies have modified times outside a sane 2020-2027 window.
  7. Every exception type PLAN.md Phase 2 needs is present in the key.
  8. The ZIP opens and contains the expected 2 files.

Exit code 0 = gate passed; 1 = failures (listed).
Run:  python3 scripts/qc_phase0.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTAKE = ROOT / "mock-intake"

failures: list[str] = []
passes: list[str] = []


def check(ok: bool, label: str) -> None:
    (passes if ok else failures).append(label)


def sha256(path: Path) -> str:
    """Hash a file in chunks. hashlib teaching note: read in 64 KB blocks so
    huge files never need to fit in memory; update() feeds the running hash."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    key = json.loads((ROOT / "seeded-errors.json").read_text(encoding="utf-8"))
    seeded = key["seeded_errors"]

    # 1. Every seeded path exists
    for s in seeded:
        for rel in s["paths"]:
            check((ROOT / rel).is_file(), f"exists: {rel}")

    # 2. File count reconciles
    actual = sorted(p for p in INTAKE.rglob("*") if p.is_file())
    check(len(actual) == key["file_count"],
          f"file count on disk ({len(actual)}) == recorded ({key['file_count']})")

    # 3. Zero-byte files are 0 bytes
    for s in (s for s in seeded if s["type"] == "zero-byte"):
        for rel in s["paths"]:
            check((ROOT / rel).stat().st_size == 0, f"zero-byte: {rel}")

    # 4. Duplicate pairs hash-identical; no unintended hash dupes
    intended_dupes: set[str] = set()
    for s in (s for s in seeded if s["type"] == "exact-duplicate"):
        a, b = (ROOT / s["paths"][0]), (ROOT / s["paths"][1])
        same = sha256(a) == sha256(b)
        check(same, f"hash-identical pair: {s['paths'][0]} == {s['paths'][1]}")
        if same:
            intended_dupes.add(sha256(a))
    hash_counts = Counter(sha256(p) for p in actual if p.stat().st_size > 0)
    unintended = {h: n for h, n in hash_counts.items() if n > 1 and h not in intended_dupes}
    check(not unintended, "no unintended hash duplicates in the fixture")

    # 5. Extension mismatches really mismatch (magic bytes)
    magic = {".pdf": b"%PDF", ".png": b"\x89PNG", ".jpg": b"\xff\xd8\xff"}
    for s in (s for s in seeded if s["type"] == "extension-mismatch"):
        for rel in s["paths"]:
            p = ROOT / rel
            head = p.read_bytes()[:8]
            expected = magic.get(p.suffix.lower())
            # mismatch confirmed if the header does NOT match what the
            # extension promises (for .doc/.docx text fakes: no OLE/zip header)
            if expected is not None:
                check(not head.startswith(expected), f"mismatch confirmed: {rel}")
            else:
                check(not head.startswith(b"\xd0\xcf\x11\xe0") and not head.startswith(b"PK"),
                      f"mismatch confirmed (not a real Office file): {rel}")

    # 6. Date anomalies outside sane window
    lo, hi = datetime(2020, 1, 1), datetime(2027, 12, 31)
    for s in (s for s in seeded if s["type"] == "date-anomaly"):
        for rel in s["paths"]:
            mt = datetime.fromtimestamp((ROOT / rel).stat().st_mtime)
            check(not (lo <= mt <= hi), f"date anomaly confirmed ({mt.date()}): {rel}")

    # 7. Every exception type Phase 2 needs is present (the absence check)
    required = {"exact-duplicate", "near-duplicate-name", "naming-violation",
                "extension-mismatch", "zero-byte", "date-anomaly",
                "junk-file", "archive", "deep-nesting"}
    present = {s["type"] for s in seeded}
    check(required <= present, f"all required error types present (missing: {required - present or 'none'})")

    # 8. ZIP opens with expected contents
    with zipfile.ZipFile(INTAKE / "old_backup.zip") as z:
        check(len(z.namelist()) == 2 and z.testzip() is None, "zip opens, 2 entries, no corruption")

    print(f"PASS: {len(passes)}  FAIL: {len(failures)}")
    for f in failures:
        print(f"  FAIL: {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
