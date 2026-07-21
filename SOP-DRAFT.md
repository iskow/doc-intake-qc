# SOP-DRAFT.md — Document Intake QC Agent

_Draft — grows as the build makes the process real. Final pass uses the sop-builder skill._

## Purpose
The repeatable service process behind this build — what Joel would run for a paying client.

## When to use
SMEs and legal/ops teams receive messy file dumps — bad names, duplicates, wrong formats — and QC them by hand or not at all.

## Process (draft)
1. Receive intake folder (never work on originals — copy first).
2. Run inventory scan (`scan.py`) → `manifest.csv`: read-only walk recording each file's true type (from magic bytes, not the extension), size, SHA-256 hash, and dates. Archives are logged as single files, not expanded. The scan never modifies originals — proven in code by a before/after hash of every file.
3. Run validation rules (`rules.py`) → `exceptions.csv`: nine deterministic checks over the manifest, one row per (file, rule) so a file that trips several issues shows each on its own line. See "What we check" below.
4. Run AI classification (`classify.py`) → `classifications.csv`: each readable document is labeled **invoice / contract / correspondence / report / other** by a language model running **on the local machine** — no document is uploaded anywhere. Files with no readable text (photos, archives, empty files) are reported `unclassified` rather than guessed. Then rerun `rules.py`, which folds the classification findings into the same `exceptions.csv` the client already reviews. See "What the classifier checks" below.
5. Generate QC report; review exceptions.
6. Apply renames/organization to the copy; deliver report + organized set.

## What we check (the nine rules)
Proven in Phase 2. Severity reflects what a missed flag would cost: **high** = data integrity (the review set is wrong), **medium** = needs a human decision before proceeding, **low** = housekeeping.

| Rule ID | Severity | Catches | How |
|---|---|---|---|
| `EXACT_DUP` | high | Byte-for-byte duplicates | Files sharing a SHA-256. Zero-byte files are excluded — every empty file shares the same hash, so they'd falsely pair. |
| `EXT_MISMATCH` | high | An extension that lies about the content | Compares the extension against the magic-byte type. Skips junk files and empty/unknown content (nothing reliable to compare). |
| `ZERO_BYTE` | high | Empty files — failed save or interrupted transfer | `size_bytes == 0` or type `empty`. |
| `NEAR_DUP_NAME` | medium | Same base name, **different** content — version ambiguity | Collapses copy/version decorations (`_v2`, ` (1)`, `Copy of`, ` - Copy`) and flags groups whose members disagree on hash. Same-hash twins are `EXACT_DUP` instead. |
| `DATE_ANOMALY` | medium | Impossible modified dates | Modified before 2000 or in the future. Creation time isn't portable — we check **modified** only. |
| `JUNK_FILE` | medium | OS/app litter that shouldn't ship | `~$` temp/lock files, `Thumbs.db`, `.DS_Store`, `desktop.ini`. |
| `ARCHIVE` | medium | Containers whose contents are invisible | Archive extensions (`.zip`, `.rar`, …). The pipeline never silently expands them — expansion is the client's call. OOXML files are zips internally but are not archives in this sense. |
| `NAMING` | low | Convention violations | Spaces, non-lowercase extension, ad-hoc status words (`FINAL`, `use this one`, `!!`), scanner-default names (`scan_001`). |
| `DEEP_NESTING` | low | Files buried where manual review misses them | More than two folders below the intake root. |

**Design note for clients:** a file can trip several rules and gets one row per rule — nothing is hidden behind a single "worst" label. Rules are deterministic and explainable; every flag traces to a concrete check. AI classification (Phase 3) sits *on top* of this, never underneath it.

## What the classifier checks (Phase 3)
Proven in Phase 3. The classifier reads **document text only** — never the filename, never the folder. That is deliberate: it keeps the folder as an independent second opinion, which is what makes the conflict check possible.

| Rule ID | Severity | Catches | How |
|---|---|---|---|
| `UNCLASSIFIED` | medium | Documents the pipeline could not label | No readable text (photos, archives, empty files, binary junk) or a model failure. Surfaced for manual review — **never** given a guessed label. |
| `CLASS_CONFLICT` | medium | A document filed in the wrong folder | The label read from the content disagrees with the folder it sits in. Two independent signals that must agree — the second-reviewer principle. |
| `LOW_CONFIDENCE` | medium | Classifications a human should verify | Model confidence below 0.75. |

**Talk to clients about these three things:**
- **Nothing leaves the machine.** The model runs locally, so a confidential intake is never uploaded to a third-party API. For legal, compliance, and bookkeeping clients this is usually the deciding fact.
- **The tool never guesses.** A photo has no correct label, so it gets none. "Unclassified" is a finding, not a gap — it becomes a short manual-review queue instead of a plausible-looking wrong answer buried in the results.
- **Accuracy must be re-measured per client.** The 100% figure is on a generated fixture of short, clean documents. Real intakes bring scans, OCR noise, and document types outside the five labels. Measure on a sample of the client's own files before quoting any number.

## Tools
- See PLAN.md kickoff block (stack). Update here when tools firm up.
- Phase 2 added **pandas** (grouping the manifest by hash and by normalized name). `pypdf` / `python-docx` / `openpyxl` read embedded document authors.
- Phase 3 added **Ollama** running `gemma4:12b` locally. No new Python packages — the API call uses the standard library's `urllib`, and text extraction reuses the same three readers Phase 2 already installed.

## QC checks
- See PLAN.md phase gates. Promote the ones that survive into permanent SOP checks.

## Open questions
- _Log here as they come up during the build._
- ~~Phase 0 surfaced the exception taxonomy...~~ **Resolved in Phase 2** — all nine types are now implemented rules; see "What we check" above.
- Date checks can only rely on *modified* dates (creation time isn't portable) — set that expectation with clients.
- The naming convention is currently inferred from the fixture (`INV-YYYY-NNN_Vendor`, ISO-dated correspondence). For a real client, **agree the convention with them first** and tune the `NAMING` signals to it — otherwise the rule either over-flags their house style or misses violations.
- The date window (before 2000 / in the future) is a sensible default, not a universal truth. Confirm the client's real matter date range and narrow it — a 2015 file in a 2024 matter is an anomaly worth catching that today's rule would pass.
- Archive expansion is deliberately manual. Decide with the client whether the engagement includes expanding and re-scanning archive contents.
- The five labels came from this fixture. **Agree the label set with the client first** — a bookkeeper may need "receipt" and "bank statement" as their own classes, and a law firm may need "pleading". Adding a label means re-measuring accuracy, not just editing the prompt.
- The model reports high confidence on nearly everything, so `LOW_CONFIDENCE` rarely fires on clean text. Do not sell "confidence scoring" as the safety net — `UNCLASSIFIED` and `CLASS_CONFLICT` are the checks doing real work.
- Scanned image-only PDFs will come back `unclassified` (no text layer). If a client's intake is mostly scans, the pipeline needs an OCR step ahead of classification — scope and price that separately.
