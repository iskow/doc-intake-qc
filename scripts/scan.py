#!/usr/bin/env python3
"""Phase 1 — Inventory scanner for the Document Intake QC Agent.

Walks the intake folder READ-ONLY and writes manifest.csv: one row per file with
path, name, extension, true file type (from magic bytes, not the extension),
size in bytes, SHA-256 hash, created/modified timestamps, and the embedded
document author (PDF /Author, DOCX/XLSX creator) where the format carries one.

Archives (e.g. old_backup.zip) are inventoried as SINGLE files — the scanner
records the zip itself and does NOT look inside. Extracting archives is the
user's call; the pipeline never silently expands them.

Read-only guarantee: files under the intake are only ever opened with mode
'rb' (read binary). The scanner writes exactly two files — manifest.csv and
intake-root.txt, both at the project root — and never modifies anything in the
intake folder.

The intake defaults to the shipped fixture, mock-intake/, and --input points it
anywhere else. The resolved folder is recorded in intake-root.txt so the later
steps organize the same collection this manifest describes; see intake.py for
why that record exists rather than a flag on each script.

Run:  py scripts/scan.py                        (Windows launcher; python3 elsewhere)
      py scripts/scan.py --input D:\\ClientData\\Acme
Output: manifest.csv and intake-root.txt at the project root.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import logging
from datetime import datetime
from pathlib import Path

from intake import add_input_arg, record_intake, resolve_intake, to_manifest_path

# Author-reading libs. Imported at top so a missing dependency fails loudly at
# startup, not halfway through a scan. All three are installed under `py`.
from docx import Document                     # python-docx: DOCX core properties
from openpyxl import load_workbook            # openpyxl: XLSX core properties
from pypdf import PdfReader                   # pypdf: PDF /Author metadata

# pypdf logs 'invalid pdf header' / 'EOF marker not found' when it meets a file
# that claims .pdf but isn't one (the fixture's text-as-pdf). We catch that as
# an exception in read_author and record Unassigned, so silence the noisy log —
# it's expected behaviour, not a problem the operator needs to see.
logging.getLogger("pypdf").setLevel(logging.ERROR)

# pathlib teaching note: Path(__file__).resolve().parents[1] climbs from
# scripts/scan.py -> scripts -> project root, so paths work no matter where
# the script is run from.
ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifest.csv"

# Magic-byte signatures: the first bytes of a file reveal its real type, no
# matter what the name says. Important caveat — .docx, .xlsx and .zip are ALL
# ZIP containers under the hood, so the bytes alone can't tell them apart; we
# report "zip" for the whole family and let Phase 2's extension rule decide
# whether that disagrees with the filename.
SIGNATURES: list[tuple[bytes, str]] = [
    (b"%PDF", "pdf"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"PK\x03\x04", "zip"),          # also .docx / .xlsx (OOXML files are zips)
    (b"\xd0\xcf\x11\xe0", "ole"),    # legacy Office (.doc/.xls) and Thumbs.db
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
]

# Columns of manifest.csv, in order. Defined once so the header and every row
# stay in lockstep.
FIELDS = [
    "path", "name", "extension", "true_type",
    "size_bytes", "sha256", "created", "modified", "author",
]

# Sentinel for "this file carries no readable document author." Used when the
# format can't hold one (txt/csv/png/zip/.doc), when the field is empty, or
# when the file won't parse as its claimed type (e.g. a .pdf that's really
# text). We never guess an author — Unassigned is the honest answer.
UNASSIGNED = "Unassigned"


def hash_and_head(path: Path, head_len: int = 2048) -> tuple[str, bytes]:
    """One pass over the file: return its SHA-256 hex digest AND the first
    `head_len` bytes (for magic-byte typing).

    hashlib teaching note: we read the file in 64 KB blocks and feed each to
    update(), so a multi-GB file never has to fit in memory at once. We grab
    the first block's leading bytes on the way past instead of re-reading the
    file just to sniff its type. A 0-byte file skips the loop entirely, so it
    gets the empty-string hash and an empty head.
    """
    h = hashlib.sha256()
    head = b""
    with path.open("rb") as f:  # 'rb' = read-only, binary — never writes
        for block in iter(lambda: f.read(65536), b""):
            if not head:
                head = block[:head_len]
            h.update(block)
    return h.hexdigest(), head


def true_type(head: bytes) -> str:
    """Classify a file by its leading bytes. Falls back to 'empty' for 0-byte
    files, 'text' for anything that decodes as UTF-8, else 'unknown'.

    Caveat: we only decode the head sample, so a valid UTF-8 file whose
    multi-byte character straddles the 2048-byte cut *could* misread as
    'unknown'. The fixture's text files are short ASCII, so this is safe here;
    a production version would decode with a tolerant boundary check.
    """
    if not head:
        return "empty"
    for sig, name in SIGNATURES:
        if head.startswith(sig):
            return name
    try:
        head.decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        return "unknown"


def read_author(path: Path) -> str:
    """Return the embedded document author, or UNASSIGNED if there isn't one.

    Only three formats carry an author we can read: PDF (/Author), DOCX and
    XLSX (the OOXML core-properties creator). We dispatch on the *claimed*
    extension, then guard every parse in try/except — a file that lies about
    its type (a .pdf that's really text, an empty .docx, the ~$ junk file)
    raises inside the library and we fall back to UNASSIGNED rather than crash.

    Teaching note: an empty author string is treated the same as no author.
    reportlab blanks unauthored PDFs to "" on purpose (see DECISIONS 2026-07-20),
    and python-docx/openpyxl default the creator to "" when unset — so we map
    any blank result to UNASSIGNED to stay consistent with the answer key.
    """
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            # pypdf reads only the metadata dictionary here, not the page text.
            meta = PdfReader(str(path)).metadata
            author = meta.author if meta else None
        elif ext == ".docx":
            author = Document(str(path)).core_properties.author
        elif ext == ".xlsx":
            author = load_workbook(str(path), read_only=True).properties.creator
        else:
            # txt, csv, png, jpg, zip, legacy .doc/.xls, Thumbs.db, etc. — no
            # readable author field. Not an error, just Unassigned.
            return UNASSIGNED
    except Exception:
        # Corrupt, empty, or mislabeled file that won't parse as its extension.
        return UNASSIGNED
    author = (author or "").strip()
    return author if author else UNASSIGNED


def scan_file(path: Path, intake: Path) -> dict:
    """Build one manifest row for a single file."""
    st = path.stat()
    digest, head = hash_and_head(path)
    # stat teaching note: st_mtime is the last-modified time everywhere. On
    # Windows st_ctime is the CREATION time (what we want); on Unix it's the
    # inode-change time. The fixture only tampers with mtime, and Phase 2's
    # date rule checks mtime, so 'created' here is informational.
    return {
        "path": to_manifest_path(path, intake),
        "name": path.name,
        "extension": path.suffix,          # kept raw ('.PDF' vs '.pdf' matters to Phase 2)
        "true_type": true_type(head),
        "size_bytes": st.st_size,
        "sha256": digest,
        "created": datetime.fromtimestamp(st.st_ctime).isoformat(timespec="seconds"),
        "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        "author": read_author(path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inventory an intake folder into manifest.csv (read-only).")
    add_input_arg(parser)
    intake = resolve_intake(parser.parse_args().input)

    # rglob("*") walks every path recursively; keep only real files (skips the
    # folder entries themselves). sorted() gives a stable, reviewable order.
    files = sorted(p for p in intake.rglob("*") if p.is_file())
    rows = [scan_file(p, intake) for p in files]

    with MANIFEST.open("w", newline="", encoding="utf-8") as f:
        # newline="" is the documented way to write CSV on Windows — without it
        # the csv module's own line endings collide with the OS and you get a
        # blank row between every record.
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    # Recorded only after the manifest is safely written, so a crashed scan
    # never leaves a record pointing at a folder no manifest describes.
    record_intake(intake)

    print(f"Scanned {len(rows)} files in {intake} "
          f"-> {MANIFEST.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
