#!/usr/bin/env python3
"""Phase 3 QC gate — prove the classification layer is accurate and honest.

Runs scan.py -> classify.py -> rules.py (so the gate always tests fresh
output), then checks:
  1. COVERAGE — one classification row per manifest file, no misses, no
     phantoms.
  2. ACCURACY — every classifiable file carries a label the answer key
     accepts. The key was generated with the fixture from the same source, so
     it cannot drift from the content.
  3. NO GUESSING — every file with no readable text is reported as
     unclassified. A confident label on a photo is the failure this catches.
  4. NO SILENT DROPS — the inverse: no file that HAS readable text was left
     unclassified.
  5. NEVER SILENTLY ACCEPTED — every unclassified, conflicting, or
     low-confidence document appears in exceptions.csv. The expected set is
     recomputed here from classifications.csv rather than trusted from
     rules.py, so a rule that forgot to fire is caught.
  6. WELL-FORMED — valid labels, confidence in range, no label without text.
  7. DETERMINISM — a sample of documents reclassifies to the same label.
     temperature=0 is supposed to guarantee this; the gate verifies it.
  8. LOCAL AND FREE — the model endpoint is the loopback address, so the
     Phase 3 requirement of $0 API cost holds by construction.
  9. READ-ONLY — the fixture is byte-for-byte unchanged after the whole run.

Exit code 0 = gate passed; 1 = failures (listed).
Run:  py scripts/qc_phase3.py        (Ollama must be running)
"""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

from classify import LABELS, MIN_CHARS, MODEL, OLLAMA_URL, UNCLASSIFIED, classify_row
from rules import CONFIDENCE_FLOOR

ROOT = Path(__file__).resolve().parents[1]
INTAKE = ROOT / "mock-intake"
MANIFEST = ROOT / "manifest.csv"
EXCEPTIONS = ROOT / "exceptions.csv"
CLASSIFICATIONS = ROOT / "classifications.csv"

# How many documents to reclassify for the determinism check. A sample keeps
# the gate quick; the point is to prove temperature=0 is holding, and a
# non-deterministic model would show up well inside eight documents.
DETERMINISM_SAMPLE = 8

CLASS_RULES = {"UNCLASSIFIED", "CLASS_CONFLICT", "LOW_CONFIDENCE"}

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
    return {p.relative_to(ROOT).as_posix(): sha256(p)
            for p in INTAKE.rglob("*") if p.is_file()}


def main() -> int:
    key = json.loads((ROOT / "seeded-errors.json").read_text(encoding="utf-8"))
    truth: dict[str, list[str] | None] = key["classes"]

    before = fixture_fingerprint()
    for script in ("scan.py", "classify.py", "rules.py"):
        subprocess.run([sys.executable, str(ROOT / "scripts" / script)], check=True)
    after = fixture_fingerprint()

    # --- 9. READ-ONLY --------------------------------------------------------
    check(before == after,
          "read-only: fixture byte-for-byte unchanged after scan+classify+rules")

    manifest = pd.read_csv(MANIFEST, dtype={"extension": str}, keep_default_na=False)
    cls = pd.read_csv(CLASSIFICATIONS, keep_default_na=False)
    exc = pd.read_csv(EXCEPTIONS, keep_default_na=False)

    # --- 1. COVERAGE ---------------------------------------------------------
    manifest_paths = set(manifest["path"])
    cls_paths = set(cls["path"])
    disk_count = sum(1 for p in INTAKE.rglob("*") if p.is_file())

    check(len(cls) == len(manifest) == disk_count,
          f"classification rows ({len(cls)}) == manifest ({len(manifest)}) "
          f"== disk ({disk_count})")
    missing = sorted(manifest_paths - cls_paths)
    check(not missing, f"every manifest file was classified ({len(manifest_paths)} files)")
    for m in missing:
        failures.append(f"  -> never classified: {m}")
    phantom = sorted(cls_paths - manifest_paths)
    check(not phantom, f"no phantom classification rows (phantom: {phantom or 'none'})")

    check(not cls.duplicated(subset=["path"]).any(),
          "no duplicate classification rows")

    # --- 2. ACCURACY ---------------------------------------------------------
    # Only files the answer key says are classifiable are scored. Files with a
    # null class have no correct label — they are check 3's business.
    label_by_path = dict(zip(cls["path"], cls["label"]))
    status_by_path = dict(zip(cls["path"], cls["status"]))
    scored = {p: accepted for p, accepted in truth.items() if accepted}
    wrong: list[str] = []
    for path, accepted in scored.items():
        got = label_by_path.get(path)
        if got not in accepted:
            wrong.append(f"{path}: expected one of {accepted}, got '{got}'")
    hits = len(scored) - len(wrong)
    check(not wrong, f"classification accuracy {hits}/{len(scored)} on classifiable files")
    for w in wrong:
        failures.append(f"  -> misclassified: {w}")

    # --- 3. NO GUESSING ------------------------------------------------------
    # Files with no readable text must NOT carry a label. This is the check
    # that would have caught the binary-garbage extraction bug: a PNG scraped
    # into 541 characters of noise would come back labeled, not unclassified.
    no_text_truth = [p for p, accepted in truth.items() if not accepted]
    guessed = [f"{p} -> '{label_by_path.get(p)}'" for p in no_text_truth
               if label_by_path.get(p) != UNCLASSIFIED]
    check(not guessed,
          f"no label invented for the {len(no_text_truth)} files with no readable text")
    for g in guessed:
        failures.append(f"  -> guessed a label on an unreadable file: {g}")

    # --- 4. NO SILENT DROPS --------------------------------------------------
    dropped = [p for p in scored if label_by_path.get(p) == UNCLASSIFIED]
    check(not dropped,
          f"no classifiable file was left unclassified ({len(scored)} files)")
    for d in dropped:
        failures.append(f"  -> readable file left unclassified: {d}")

    # --- 5. NEVER SILENTLY ACCEPTED ------------------------------------------
    # Recompute what SHOULD be flagged straight from classifications.csv. This
    # deliberately does not reuse rules.py's logic — a gate that asks the same
    # code that produced the answer is only testing that it is consistent with
    # itself.
    expected: set[tuple[str, str]] = set()
    for _, rec in cls.iterrows():
        if rec["status"] != "ok":
            expected.add((rec["path"], "UNCLASSIFIED"))
            continue
        hint = str(rec["folder_hint"]).strip()
        if hint and rec["label"] != hint:
            expected.add((rec["path"], "CLASS_CONFLICT"))
        if float(rec["confidence"]) < CONFIDENCE_FLOOR:
            expected.add((rec["path"], "LOW_CONFIDENCE"))

    actual = {(p, r) for p, r in zip(exc["path"], exc["rule_id"]) if r in CLASS_RULES}
    check(expected == actual,
          f"every unclassified/conflicting/low-confidence doc is in exceptions.csv "
          f"({len(expected)} rows)")
    for miss in sorted(expected - actual):
        failures.append(f"  -> missing from exceptions.csv: {miss[1]} on {miss[0]}")
    for extra in sorted(actual - expected):
        failures.append(f"  -> unexpected exception row: {extra[1]} on {extra[0]}")

    # Severity and detail hygiene on the classification rows we added.
    class_rows = exc[exc["rule_id"].isin(CLASS_RULES)]
    check(set(class_rows["severity"]) <= {"medium"},
          "classification exceptions all carry a valid severity")
    check(all(str(d).strip() for d in class_rows["detail"]),
          "every classification exception has a non-empty detail")

    # --- 6. WELL-FORMED ------------------------------------------------------
    bad_labels = sorted(set(cls["label"]) - set(LABELS) - {UNCLASSIFIED})
    check(not bad_labels, f"all labels are from the fixed list (unexpected: {bad_labels or 'none'})")

    confs = cls["confidence"].astype(float)
    check(bool(((confs >= 0) & (confs <= 1)).all()), "all confidence values are within 0.0-1.0")

    # A label must be backed by text. Anything labeled with fewer than
    # MIN_CHARS characters means the pipeline classified something it could
    # not actually read.
    labeled_without_text = cls[(cls["status"] == "ok") & (cls["chars"] < MIN_CHARS)]
    check(labeled_without_text.empty,
          f"no file was labeled without readable text (>= {MIN_CHARS} chars)")
    for p in labeled_without_text["path"]:
        failures.append(f"  -> labeled with no text: {p}")

    check(all(str(r).strip() for r in cls.loc[cls["status"] != "ok", "reason"]),
          "every unclassified row explains why")

    # --- 7. DETERMINISM ------------------------------------------------------
    # Reclassify a spread of readable documents and confirm the labels repeat.
    with MANIFEST.open(encoding="utf-8") as f:
        records = [r for r in csv.DictReader(f)
                   if status_by_path.get(r["path"]) == "ok"]
    step = max(1, len(records) // DETERMINISM_SAMPLE)
    sample = records[::step][:DETERMINISM_SAMPLE]
    drifted: list[str] = []
    for rec in sample:
        again = classify_row(rec)
        if again["label"] != label_by_path[rec["path"]]:
            drifted.append(f"{rec['path']}: {label_by_path[rec['path']]} -> {again['label']}")
    check(not drifted,
          f"determinism: {len(sample)} sampled documents reclassify to the same label")
    for d in drifted:
        failures.append(f"  -> label drifted between runs: {d}")

    # --- 8. LOCAL AND FREE ---------------------------------------------------
    host = urlparse(OLLAMA_URL).hostname
    check(host in {"127.0.0.1", "localhost", "::1"},
          f"model endpoint is local ({host}) — no data leaves the machine, API cost $0")

    # --- Report --------------------------------------------------------------
    tally = cls["label"].value_counts().sort_index()
    print()
    print(f"Model: {MODEL} at {OLLAMA_URL}")
    print(f"Classified {len(cls)} files | {len(scored)} classifiable "
          f"({hits} correct) | {len(no_text_truth)} with no readable text")
    print("  " + " | ".join(f"{label}={count}" for label, count in tally.items()))
    print(f"Exceptions: {len(exc)} rows total, {len(class_rows)} from classification rules")
    print()
    print(f"PASS: {len(passes)}  FAIL: {len([f for f in failures if not f.startswith('  ->')])}")
    for f in failures:
        print(f"  FAIL: {f}" if not f.startswith("  ->") else f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
