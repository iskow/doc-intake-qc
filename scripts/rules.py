#!/usr/bin/env python3
"""Phase 2 — Validation rules engine for the Document Intake QC Agent.

Reads manifest.csv (built by scan.py) and applies nine deterministic rules,
writing every violation to exceptions.csv. One row per (file, rule) — a single
file can trip several rules, and each shows up on its own line so nothing hides.

The nine rules and what each catches:
  EXACT_DUP      byte-for-byte identical files (same SHA-256), size > 0
  NEAR_DUP_NAME  same "base" name but DIFFERENT content (e.g. a _v2 beside the
                 original) — a version-ambiguity flag, not a byte duplicate
  NAMING         convention violations: spaces, non-lowercase extension, ad-hoc
                 status words ("FINAL", "use this one"), scanner-default names
  EXT_MISMATCH   the extension lies about the content (magic bytes disagree)
  ZERO_BYTE      empty file (0 bytes) — a failed save or interrupted transfer
  DATE_ANOMALY   modified date before 2000 or in the future (clock/tampering)
  JUNK_FILE      OS/app litter that should never ship (~$ temp files, Thumbs.db)
  ARCHIVE        a container (.zip etc.) whose contents are invisible until
                 expanded — must be opened before the set can be called complete
  DEEP_NESTING   buried more than two folders deep — easy to miss in review

Why deterministic rules before any AI (Phase 3): they are testable and
defensible. Every flag here traces to a concrete, explainable check — the
eDiscovery standard. AI classification is added on top, never underneath.

Three further rules run only when classifications.csv exists (Phase 3 has been
run). They judge the classifier's OUTPUT, which is why they live here with the
other judgments rather than inside classify.py — that script gathers evidence,
this one decides what is a problem:
  UNCLASSIFIED    no label could be assigned (no readable text, or the model
                  failed) — surfaced for manual review, never silently dropped
  CLASS_CONFLICT  the content says one thing and the folder says another
  LOW_CONFIDENCE  the model was unsure enough to warrant a human look

Read-only: this script READS manifest.csv (and classifications.csv when
present) and WRITES exceptions.csv. It never touches anything under
mock-intake/.

Run:  py scripts/rules.py        (Windows launcher; python3 on macOS/Linux)
Output: exceptions.csv at the project root.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifest.csv"
EXCEPTIONS = ROOT / "exceptions.csv"
CLASSIFICATIONS = ROOT / "classifications.csv"   # optional — Phase 3 output

# Below this confidence, a classification gets a human look before it is
# trusted. Measured, not guessed: on this fixture the model reports 0.9-1.0 on
# every document it can read, so this threshold fires zero times here. That is
# a real result, not a broken rule — qc_phase3.py proves the rule works by
# raising the threshold until it fires, then restoring it.
CONFIDENCE_FLOOR = 0.75

# Today's date bounds the "future date" check. Kept as a module constant so the
# rule is testable and the report can state the window it used.
TODAY = date.today()

# Severity tiers, in eDiscovery terms — how much a missed flag would cost:
#   high   = data integrity / could corrupt the review set (dupes, wrong type,
#            empty files)
#   medium = needs a human decision before proceeding (version ambiguity, bad
#            metadata, junk, hidden archive contents)
#   low    = housekeeping (naming, deep nesting)
# Output columns of exceptions.csv, defined once so header and rows stay locked.
FIELDS = ["path", "name", "rule_id", "severity", "detail"]

# --- Lookup tables ---------------------------------------------------------

# What true_type (magic-byte class from scan.py) each extension should map to.
# .docx/.xlsx/.pptx and .zip are ALL zip containers, so they share "zip" — the
# bytes can't tell them apart, and that's honest, not a miss. Extensions not in
# this table are skipped by EXT_MISMATCH (we can't judge what we don't model).
EXPECTED_TYPE: dict[str, set[str]] = {
    ".pdf": {"pdf"},
    ".png": {"png"},
    ".jpg": {"jpg"}, ".jpeg": {"jpg"},
    ".gif": {"gif"},
    ".docx": {"zip"}, ".xlsx": {"zip"}, ".pptx": {"zip"}, ".zip": {"zip"},
    ".doc": {"ole"}, ".xls": {"ole"}, ".ppt": {"ole"},
    ".txt": {"text"}, ".csv": {"text"},
}

# Extensions that are archives — flagged because their contents stay invisible
# until someone expands them. (OOXML files are also zips under the hood but are
# NOT archives in this sense, so they're excluded by matching on extension.)
ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz"}

# Known OS/application junk filenames (compared case-insensitively).
JUNK_NAMES = {"thumbs.db", ".ds_store", "desktop.ini"}

# Ad-hoc status words in a filename that signal version ambiguity / no
# convention. "draft" is deliberately NOT here — the fixture uses it in a
# legitimately-named file (RPT-2026-Q2_Operations_DRAFT.docx).
NOISE_TOKENS = ("final", "use this one", "!!")

# How deep is "too deep": more than this many folders below the intake root.
MAX_FOLDER_DEPTH = 2


# --- Small helpers ---------------------------------------------------------

def is_junk(name: str) -> bool:
    """True for Office temp/lock files (~$ prefix) and known OS junk files."""
    return name.startswith("~$") or name.lower() in JUNK_NAMES


def normalized_stem(name: str) -> str:
    """Collapse copy/version decorations so name-twins land on the same key.

    'CTR-...-BrightWorks_v2.docx' and 'CTR-...-BrightWorks.docx' both reduce to
    'ctr-...-brightworks', so NEAR_DUP_NAME can see they're the same document
    under two names. Copy artifacts ('Copy of X', 'X (1)', 'X - Copy') collapse
    too, but those turn out byte-identical, so EXACT_DUP claims them first.
    """
    stem = Path(name).stem.lower()
    stem = re.sub(r"^copy of ", "", stem)      # leading "Copy of "
    stem = re.sub(r"\s*\(\d+\)$", "", stem)     # trailing " (1)"
    stem = re.sub(r"\s*-\s*copy$", "", stem)    # trailing " - Copy"
    stem = re.sub(r"_v\d+$", "", stem)          # trailing "_v2"
    return stem.strip()


def folder_depth(path: str) -> int:
    """Number of folders between the intake root and the file.

    'mock-intake/A/B/c.pdf' -> 2 (folders A and B). Root-level files -> 0.
    """
    parts = path.split("/")
    # Drop the leading "mock-intake" and the filename; what's left is folders.
    return max(0, len(parts) - 2)


# --- The rule engine -------------------------------------------------------

def find_exceptions(df: pd.DataFrame) -> list[dict]:
    """Run all nine rules over the manifest DataFrame; return exception rows.

    pandas teaching notes are inline. The manifest is small (tens of files), so
    clarity beats cleverness — we iterate rows where that reads plainly and use
    vectorised grouping only where it genuinely helps (the duplicate hunt).
    """
    rows: list[dict] = []

    def add(rec: pd.Series, rule_id: str, severity: str, detail: str) -> None:
        rows.append({
            "path": rec["path"], "name": rec["name"],
            "rule_id": rule_id, "severity": severity, "detail": detail,
        })

    # --- EXACT_DUP: identical bytes -----------------------------------------
    # groupby('sha256') buckets rows sharing a hash. We ignore empty files
    # first: every 0-byte file has the SAME well-known empty-file hash, so
    # without this they'd falsely pair with each other. Empties are ZERO_BYTE's
    # job, not a content-duplicate.
    nonempty = df[df["size_bytes"] > 0]
    for digest, group in nonempty.groupby("sha256"):
        if len(group) > 1:
            names = sorted(group["name"])
            for _, rec in group.iterrows():
                others = [n for n in names if n != rec["name"]]
                add(rec, "EXACT_DUP", "high",
                    f"identical bytes to: {', '.join(others)}")

    # --- NEAR_DUP_NAME: same base name, different content -------------------
    # Group by the normalized stem. A group is a near-dup only if its members
    # DISAGREE on hash (same name, different bytes -> which is authoritative?).
    # Same-hash name-twins are already EXACT_DUP, so we skip those groups.
    df = df.copy()
    df["_stem"] = df["name"].map(normalized_stem)
    for stem, group in df.groupby("_stem"):
        if len(group) > 1 and group["sha256"].nunique() > 1:
            names = sorted(group["name"])
            for _, rec in group.iterrows():
                others = [n for n in names if n != rec["name"]]
                add(rec, "NEAR_DUP_NAME", "medium",
                    f"same base name, different content vs: {', '.join(others)}")

    # --- Per-file rules ------------------------------------------------------
    for _, rec in df.iterrows():
        name = rec["name"]
        ext = str(rec["extension"]).lower()
        true_type = rec["true_type"]
        junk = is_junk(name)

        # ZERO_BYTE ----------------------------------------------------------
        if rec["size_bytes"] == 0 or true_type == "empty":
            add(rec, "ZERO_BYTE", "high", "file is empty (0 bytes)")

        # JUNK_FILE ----------------------------------------------------------
        if junk:
            add(rec, "JUNK_FILE", "medium",
                "OS/application junk file — should not ship in a client set")

        # ARCHIVE ------------------------------------------------------------
        if ext in ARCHIVE_EXTS:
            add(rec, "ARCHIVE", "medium",
                "archive — contents are invisible until expanded")

        # EXT_MISMATCH -------------------------------------------------------
        # Skip junk (its extension isn't the point) and skip empty/unknown
        # content (nothing reliable to compare against). Only flag when we have
        # a modelled extension AND a concrete type that disagrees with it.
        if not junk and true_type not in ("empty", "unknown"):
            expected = EXPECTED_TYPE.get(ext)
            if expected and true_type not in expected:
                add(rec, "EXT_MISMATCH", "high",
                    f"extension '{ext}' but content is '{true_type}'")

        # DATE_ANOMALY -------------------------------------------------------
        mtime = datetime.fromisoformat(rec["modified"]).date()
        if mtime.year < 2000 or mtime > TODAY:
            add(rec, "DATE_ANOMALY", "medium",
                f"modified date {mtime.isoformat()} is out of range "
                f"(before 2000 or after {TODAY.isoformat()})")

        # DEEP_NESTING -------------------------------------------------------
        depth = folder_depth(rec["path"])
        if depth > MAX_FOLDER_DEPTH:
            add(rec, "DEEP_NESTING", "low",
                f"{depth} folders deep — easy to miss in a manual review")

        # NAMING -------------------------------------------------------------
        # Junk files are reported as JUNK_FILE, not double-counted here.
        if not junk:
            reasons = naming_reasons(name)
            if reasons:
                add(rec, "NAMING", "low", "; ".join(reasons))

    return rows


def classification_exceptions(cls: pd.DataFrame) -> list[dict]:
    """Run the three Phase 3 rules over classifications.csv.

    The design point worth understanding: classify.py never sees the folder
    name, so the model's label and the folder it was filed in are two
    INDEPENDENT pieces of evidence. When independent evidence disagrees, that
    is a finding — the same reason a second reviewer is valuable. If we had fed
    the folder into the prompt, the model would just echo it back and this rule
    could never fire.
    """
    rows: list[dict] = []
    for _, rec in cls.iterrows():
        base = {"path": rec["path"], "name": rec["name"]}

        # UNCLASSIFIED -------------------------------------------------------
        # Anything the pipeline could not label. "never silently accepted" is
        # the requirement — an unlabeled file must be visible, not absent.
        if rec["status"] != "ok":
            rows.append({**base, "rule_id": "UNCLASSIFIED", "severity": "medium",
                         "detail": f"no classification assigned — {rec['reason']}"})
            continue   # the rules below need a real label to mean anything

        # CLASS_CONFLICT -----------------------------------------------------
        hint = str(rec["folder_hint"]).strip()
        if hint and rec["label"] != hint:
            rows.append({**base, "rule_id": "CLASS_CONFLICT", "severity": "medium",
                         "detail": f"content reads as '{rec['label']}' but the file "
                                   f"sits in a '{hint}' folder"})

        # LOW_CONFIDENCE -----------------------------------------------------
        if float(rec["confidence"]) < CONFIDENCE_FLOOR:
            rows.append({**base, "rule_id": "LOW_CONFIDENCE", "severity": "medium",
                         "detail": f"model confidence {rec['confidence']} is below "
                                   f"{CONFIDENCE_FLOOR} — verify by hand"})
    return rows


def naming_reasons(name: str) -> list[str]:
    """Return the list of naming-convention problems for a filename (empty if
    the name is clean). Each reason is a concrete, explainable signal."""
    reasons: list[str] = []
    stem = Path(name).stem
    ext = Path(name).suffix

    if re.search(r"\s", name):
        reasons.append("contains spaces")
    if ext != ext.lower():
        reasons.append(f"non-lowercase extension '{ext}'")
    low = name.lower()
    hits = [t for t in NOISE_TOKENS if t in low]
    if hits:
        reasons.append(f"ad-hoc status word(s): {', '.join(hits)}")
    if re.fullmatch(r"scan_\d+", stem.lower()):
        reasons.append("scanner-default name (no document identity)")
    return reasons


def main() -> None:
    # read_csv turns the manifest into a DataFrame — a table with named columns
    # we can group and filter. keep_default_na=False stops pandas from turning
    # blank cells into NaN floats; we want plain strings throughout.
    df = pd.read_csv(MANIFEST, dtype={"extension": str}, keep_default_na=False)

    rows = find_exceptions(df)

    # Phase 3's rules only run if the classifier has been run. Keeping this
    # optional means Phase 2 still stands alone: a fresh clone can scan and
    # apply the deterministic rules without Ollama installed at all.
    if CLASSIFICATIONS.exists():
        cls = pd.read_csv(CLASSIFICATIONS, keep_default_na=False)
        rows += classification_exceptions(cls)

    out = pd.DataFrame(rows, columns=FIELDS)
    # Stable, reviewable order: by file, then rule.
    out = out.sort_values(["path", "rule_id"]).reset_index(drop=True)
    out.to_csv(EXCEPTIONS, index=False)

    n_files = out["path"].nunique() if not out.empty else 0
    print(f"Flagged {len(out)} exceptions across {n_files} files "
          f"-> {EXCEPTIONS.relative_to(ROOT).as_posix()}")
    # Quick per-rule tally so the operator sees the shape at a glance.
    if not out.empty:
        for rule_id, count in out["rule_id"].value_counts().sort_index().items():
            print(f"  {rule_id:<14} {count}")


if __name__ == "__main__":
    main()
