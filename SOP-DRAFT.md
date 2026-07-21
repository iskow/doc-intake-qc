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
5. Run the organizer (`organize.py --by class|author|custodian`) → `organized/by-<mode>/<value>/<original path>/`: every file copied into a bucketed tree that keeps its original folder structure, with created/modified dates, permissions and alternate data streams intact. **Originals never renamed, moved, or modified.** Files with no value on the chosen axis go to `Unassigned/`, never a guessed bucket. See "Organizing the set" below.
6. Generate the QC report (`report.py`) → `qc-report.html`: one self-contained file — no internet, no install, opens in any browser. **Run it last**: it reads all three cross-references, so all three `organize.py` modes must have run first. It reconciles every figure before it will render and refuses to write a report whose numbers disagree. See "The report" below.
7. Deliver the report + the organized set + the cross-reference CSV, and walk the client through the exceptions queue, the naming gap, and what the copy does and does not preserve.

**Running it: one command.** `py scripts/run_demo.py` runs steps 2–6 in the only order that works and stops at the first failure. Two things worth knowing before running it on a client's set:
- **`report.py` must run last.** It reads all three cross-references. The runner enforces this and `report.py` refuses to render without them, so the order cannot be got wrong by accident.
- **`--no-ai` runs the deterministic half** — the rules engine plus the author and custodian axes — on a machine with no model installed. Measured on the fixture: 30 findings across 23 files in 1.4 seconds. Useful when scoping a client who will not allow any model on their data, or before a model is set up.

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

## Organizing the set (Phase 4)
Proven in Phase 4a/4b. One command per axis; run all three and compare, or pick the one the client actually needs.

| Mode | Bucket comes from | Source of truth | Unassigned means |
|---|---|---|---|
| `--by class` | What the document **is** | The Phase 3 content label | No readable text — a photo, empty file, or archive. Never guessed. |
| `--by author` | Who **wrote** it | The embedded author property (PDF `/Author`, DOCX/XLSX creator) | The format can't carry an author, or the field is blank. |
| `--by custodian` | Where it was **collected from** | `custodian-map.csv` — a client-supplied collection log | No mapping rule covers the file. |

**Non-destructive, and provable.** Originals are only ever read; the tool refuses to write anywhere inside the intake folder. The QC gate re-hashes all 41 originals before and after and compares every copy to its source.

**What survives the copy, precisely** — worth being exact, because "we preserve metadata" is the kind of claim a client will hold you to:

| Metadata | Survives? | Why |
|---|---|---|
| Embedded document metadata (PDF `/Author`, Office core properties, EXIF) | **Yes** | It lives inside the file's bytes. Identical SHA-256 means identical embedded metadata, necessarily. |
| **Modified** date | **Yes** | Verified on all 41 copies in all three modes. This is what keeps the `DATE_ANOMALY` findings true on the copy. |
| **Creation** date | **Yes** (since 4b) | `robocopy /COPY:DATSO` restores it. Verified exact on all 41 copies; a negative test using a plain Python copy fails this check, so the check has teeth. |
| **Folder structure / original path** | **Yes** (since 4b) | Each file keeps its original path *under* its bucket. Where a document sat is itself evidence. |
| NTFS ACLs | **Yes** (since 4b) | Carried by `/COPY:S`. Verified by copying a file with a non-inherited permission entry and comparing the full security descriptor. **Same-volume only** — copying to another drive or a network share drops it. |
| Alternate data streams | **Yes** | Carried by robocopy, and (unexpectedly) by Python's copy too — it delegates to the Win32 `CopyFile2` API. |
| **Owner** | **Not proven** | The check passes, but it passes on a machine where every file has the same owner, so it only shows the owner isn't clobbered. Carrying a *foreign* owner needs elevated privilege. Do not claim this to a client without testing on their data. |
| **Accessed** date | **No — and it cannot be** | Reading a file updates its access time, so our own hashing in step 2 destroys the original before the copy exists. NTFS also writes access times to disk lazily. See below. |

**Access time is a collection-stage problem, not a processing-stage one.** If a matter needs last-access dates, they must be captured by the forensic collection tool **at acquisition**, before any processing reads the files. No copy-based workflow can recover them afterwards — and any tool that claims to preserve them through a hash-and-copy pipeline is either not hashing or not telling you the truth. Say this early; it is the kind of thing that gets discovered late and expensively.

**`manifest.csv` is still the load file and still authoritative.** Dates are captured from the originals at scan time, and it remains the record to cite. The difference since 4b is that the delivered copy no longer *contradicts* it on created and modified dates.

**Every run writes a cross-reference** — `organized/crossref-by-<mode>.csv`, one row per file: original path, original folder, bucket, new path, hash. Once files are re-foldered this is the only record of where each document sat in the client's environment. Ship it with the organized set; it is not optional.

**A filename collision stops the run.** The tool will not auto-rename a file to resolve a clash, because a processed filename that no longer matches what was collected is worse than a stopped run. It reports every clash with both source paths and copies nothing, so the operator fixes them in one pass.

**Talk to clients about these four things:**
- **The three axes are not interchangeable.** Author is not the party named in the filename, and neither is the custodian. A service agreement can name one company, be written by a second, and be collected from a third. Ask which question they actually need answered before choosing a mode.
- **The custodian map is theirs, not ours.** The tool cannot verify it — a wrong map produces confidently wrong buckets. Agree it in writing at the start of the engagement; it is the collection log, and it is a scoping deliverable.
- **"Organized" does not mean "renamed."** Files are copied under their original names on purpose, so every copy traces back to what the client sent. Bad filenames are **reported and not fixed**. If they want remediation, that is a separate, agreed step — and the cross-reference table (now written on every run) is what keeps the change auditable.
- **Nothing is thrown away or merged.** Every file appears exactly once in every mode, including the junk and the empties. Deleting is always the client's decision.

## The report
Built in Phase 4c. One file, `qc-report.html`, no external assets — it has to open on a client machine with no network, and the QC gate enforces that (no `<script>`, no remote stylesheet, no `@import`, no remote asset).

What it contains, in order: the headline (how many files need attention), stat tiles by severity, **one queue** holding deterministic *and* AI findings together in severity order, a plain-language gloss of every rule, the classification breakdown, the three-axis comparison with the divergence document, date fidelity, where the cross-reference lives, the reconciliation table, and the limitations.

Rules that hold for any client version:
- **One queue, not two.** Deterministic findings and classification findings go in the same list. Splitting them produces two reports nobody cross-references, and the client has to work out for themselves that a file appears in both.
- **The report reconciles itself before it renders.** Flagged + clean must equal received; severities must account for every finding; each mode's cross-reference must hold every file. A mismatch **stops the render** — never a caveat printed next to a wrong total.
- **Numbers are re-measured, not remembered.** Date fidelity is recomputed against the pre-copy record every run (246 comparisons on this fixture), so the claim cannot outlive the thing it describes.
- **The limitations section is mandatory and is checked by the build.** Access times not preserved and why; ownership unproven; naming reported-not-fixed; the custodian map is a client input; fixture accuracy does not predict client accuracy. For these, the failure mode is *omission* — so their presence is asserted in the gate, not left to a proofread.
- **Severity never rides on color alone.** One hue getting darker (it is a tier, not a set of categories), with the word always printed — it has to survive a colorblind reader, a grayscale printout, and a bad projector.
- **Validate the palette, never eyeball it.** Two of Joel's own brand colors failed on measurement (1.69:1 and 2.44:1) and had to be darkened. Run the dataviz validator before changing any color.

## Tools
- See PLAN.md kickoff block (stack). Update here when tools firm up.
- Phase 2 added **pandas** (grouping the manifest by hash and by normalized name). `pypdf` / `python-docx` / `openpyxl` read embedded document authors.
- Phase 3 added **Ollama** running `gemma4:12b` locally. No new Python packages — the API call uses the standard library's `urllib`, and text extraction reuses the same three readers Phase 2 already installed.
- Phase 4 added **nothing**. `organize.py` and `report.py` are pure standard library; the copy engine is Windows' own `robocopy`. The report is hand-written HTML and inline SVG — deliberately no chart library, because a client-facing file that fetches a script from the internet stops working the moment they open it offline.

## QC checks
- See PLAN.md phase gates. Promote the ones that survive into permanent SOP checks.

## Open questions
- _Log here as they come up during the build._
- ~~Phase 0 surfaced the exception taxonomy...~~ **Resolved in Phase 2** — all nine types are now implemented rules; see "What we check" above.
- Date **checks** still rely on *modified* dates only — the fixture seeds anomalies on mtime, and mtime is the field that travels. (Creation dates are now preserved on the copy since 4b; that is a separate thing from what the rules test.)
- The naming convention is currently inferred from the fixture (`INV-YYYY-NNN_Vendor`, ISO-dated correspondence). For a real client, **agree the convention with them first** and tune the `NAMING` signals to it — otherwise the rule either over-flags their house style or misses violations.
- The date window (before 2000 / in the future) is a sensible default, not a universal truth. Confirm the client's real matter date range and narrow it — a 2015 file in a 2024 matter is an anomaly worth catching that today's rule would pass.
- Archive expansion is deliberately manual. Decide with the client whether the engagement includes expanding and re-scanning archive contents.
- The five labels came from this fixture. **Agree the label set with the client first** — a bookkeeper may need "receipt" and "bank statement" as their own classes, and a law firm may need "pleading". Adding a label means re-measuring accuracy, not just editing the prompt.
- The model reports high confidence on nearly everything, so `LOW_CONFIDENCE` rarely fires on clean text. Do not sell "confidence scoring" as the safety net — `UNCLASSIFIED` and `CLASS_CONFLICT` are the checks doing real work.
- **Who supplies the custodian map, and when?** It is a client deliverable the tool depends on. Build it into intake/kickoff — chasing it after the scan blocks the organize step.
- **Does the engagement include renaming?** Currently out of scope by design (traceability over tidiness). If a client wants it, price it separately and insist on the cross-reference table; without one, the organized set becomes unauditable.
- Archives stay unexpanded, so their contents appear in no bucket in any mode — the `ARCHIVE` exception is the only trace. Confirm with the client whether expansion is in scope (same open question as Phase 2, now with a second consequence).
- Scanned image-only PDFs will come back `unclassified` (no text layer). If a client's intake is mostly scans, the pipeline needs an OCR step ahead of classification — scope and price that separately.
